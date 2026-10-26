import asyncio
import copy
import json
import os
import re
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import httpx
from jsonschema import validate

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'));sys.path.insert(0,str(ROOT/'space'))
from weekend_agent.server import create_pipeline
from weekend_agent.domain.planning import PlanError, normalize, minutes
from weekend_agent.domain.understanding import demo_request, extract
from weekend_agent.service.pipeline import Session
from app import build_app

class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env=patch.dict(os.environ,{'MODEL_API_KEY':'','AMAP_KEY':'','TAVILY_KEY':'','EVENT_FEED_URL':'','EVENT_FEED_TOKEN':'','EVENT_FEED_PATH':''});self.env.start()
        self.pipeline,self.client=create_pipeline(httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500))))
        self.session=Session();self.events=[]
    async def asyncTearDown(self):
        await self.client.aclose();self.env.stop()
    async def run_turn(self,**payload):
        return await self.pipeline.turn(self.session,payload,lambda *args:self.events.append(args))

    async def test_demo_has_valid_protocol_and_complete_routes(self):
        reply=await self.run_turn(action='demo')
        self.assertEqual(reply['intent'],'plan')
        self.assertEqual(len(reply['next_step']['result']['plans']),3)
        validate(reply,self.pipeline.response_schema)
        self.assertEqual(set(reply),{'schema_version','intent','message','next_step'})
        self.assertTrue(any(e[0]=='search' and '示例' in e[2] for e in self.events))

    async def test_multi_turn_collects_missing_information(self):
        self.pipeline.places.key='fake'
        async def responder(request):return live_handler(request)
        self.client._transport=httpx.MockTransport(responder)
        reply=await self.run_turn(input='杭州两个人，这周六想看展、吃火锅')
        self.assertEqual(reply['intent'],'clarify');self.assertIn('每人预算',reply['message'])
        reply=await self.run_turn(input='我从武林广场出发，朋友从西湖文化广场出发，都在12:00到20:00有空，每人预算180元')
        self.assertEqual(reply['intent'],'plan')
        self.assertEqual(self.session.slots['people'][1]['location'],'西湖文化广场')

    async def test_roundtrip_and_budget_constraints(self):
        reply=await self.run_turn(action='demo')
        result=reply['next_step']['result']
        for plan in result['plans']:
            self.assertLessEqual(plan['budgetPerPerson']['total'],180)
            self.assertLessEqual(plan['totalDistanceKm'],3)
            self.assertEqual(len(plan['stops']),3)
            for arrival in plan['arrivals']:
                self.assertGreaterEqual(minutes(plan['startTime'])-arrival['minutes'],12*60)
                self.assertLessEqual(minutes(plan['endTime'])+arrival['returnMinutes'],20*60)
            self.assertTrue(all(s['source']['checkedAt'] is None for s in plan['stops']))

    async def test_revise_budget_is_replanned(self):
        await self.run_turn(action='demo')
        reply=await self.run_turn(input='预算改成130元')
        self.assertEqual(reply['intent'],'plan')
        plans=reply['next_step']['result']['plans'];self.assertEqual(len(plans),1)
        self.assertLessEqual(plans[0]['budgetPerPerson']['total'],130)

    async def test_remove_preference_changes_actual_stops(self):
        await self.run_turn(action='demo')
        reply=await self.run_turn(input='不要火锅，想喝咖啡')
        self.assertEqual(reply['intent'],'plan')
        for plan in reply['next_step']['result']['plans']:
            self.assertNotIn('火锅',plan['tags']);self.assertIn('咖啡',plan['tags'])

    async def test_select_second_route_and_invite(self):
        await self.run_turn(action='demo')
        second=self.session.result['plans'][1]
        reply=await self.run_turn(input='选第二条，生成邀请函')
        self.assertEqual(reply['intent'],'invite')
        self.assertEqual(reply['next_step']['selected_plan_id'],second['id'])
        self.assertIn('示例路线',reply['next_step']['invitation']);self.assertIn('不代表已订票',reply['next_step']['invitation'])

    async def test_invite_unknown_id_is_recoverable(self):
        await self.run_turn(action='demo')
        self.assertEqual((await self.run_turn(action='invite',plan_id='made-up'))['intent'],'error')

    async def test_cannot_invite_before_planning(self):
        self.assertEqual((await self.run_turn(input='生成邀请函'))['intent'],'clarify')

    async def test_zero_budget_keeps_old_result_but_marks_failure(self):
        await self.run_turn(action='demo');old=copy.deepcopy(self.session.result)
        reply=await self.run_turn(input='每人预算0元')
        self.assertEqual(reply['intent'],'error');self.assertEqual(self.session.result,old)
        self.assertEqual(reply['next_step']['slots']['budget'],0)

    async def test_unknown_city_not_replaced(self):
        await self.run_turn(action='demo')
        reply=await self.run_turn(input='改去上海')
        self.assertEqual(reply['intent'],'clarify');self.assertEqual(reply['next_step']['slots']['city'],'上海');self.assertIsNone(reply['next_step']['result']);self.assertNotIn('people',reply['next_step']['slots'])

    async def test_time_conflict_is_not_hidden(self):
        req=demo_request();req['people'][1]['availableFrom']='20:00';req['people'][1]['availableTo']='23:00'
        self.assertEqual((await self.run_turn(request=req))['intent'],'error')

    async def test_booking_is_out_of_scope(self):
        reply=await self.run_turn(input='帮我订票并付款')
        self.assertEqual(reply['intent'],'out_of_scope');self.assertIsNone(self.session.result)

    async def test_total_budget_changes_actual_plan_allowance(self):
        await self.run_turn(action='demo')
        reply=await self.run_turn(input='总预算300元')
        self.assertEqual(reply['intent'],'plan')
        self.assertEqual(reply['next_step']['slots']['budget'],150)
        self.assertTrue(all(p['budgetPerPerson']['total']*2<=300 for p in self.session.result['plans']))

    async def test_ambiguous_times_pause_and_clear_previous_invitable_result(self):
        await self.run_turn(action='demo')
        reply=await self.run_turn(input='12:00到18:00，14:00到20:00')
        self.assertEqual(reply['intent'],'clarify');self.assertIsNone(reply['next_step']['result'])
        self.assertIn('对应到每个人',reply['message'])
        self.assertEqual((await self.run_turn(action='invite'))['intent'],'clarify')
        self.assertEqual((await self.run_turn(input='重新生成路线'))['intent'],'clarify')
        reply=await self.run_turn(input='我12:00到20:00，朋友12:00到20:00')
        self.assertEqual(reply['intent'],'plan')

    async def test_total_without_people_requests_clarification(self):
        reply=await self.run_turn(input='上海周六看展，总预算300元')
        self.assertEqual(reply['intent'],'clarify');self.assertIn('同行人数',reply['message'])
        self.assertNotIn('budget',reply['next_step']['slots'])

    async def test_map_outage_does_not_substitute_a_sample(self):
        self.pipeline.places.key='fake'
        reply=await self.run_turn(request=demo_request())
        self.assertEqual(reply['intent'],'error');self.assertIsNone(reply['next_step']['result'])

    async def test_model_invalid_json_retries_then_uses_rules(self):
        calls=[]
        def handler(request):
            calls.append(request);return httpx.Response(200,json={'choices':[{'message':{'content':'not-json'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.pipeline.runner.client=client;self.pipeline.runner.key='fake'
            reply=await self.run_turn(input='杭州两个人，这周六想看展')
        self.assertEqual(len(calls),2);self.assertEqual(reply['intent'],'clarify')
        self.assertTrue(reply['next_step']['notices'])

    async def test_model_output_is_schema_checked_before_merge(self):
        decision={'intent':'update','slots':{'city':'杭州','invented_field':'bad'},'message':'','plan_id':None}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(decision)}}]}))) as client:
            self.pipeline.runner.client=client;self.pipeline.runner.key='fake'
            await self.run_turn(input='杭州两个人')
        self.assertNotIn('invented_field',self.session.slots)

    async def test_fresh_sessions_do_not_share_locations(self):
        await self.run_turn(action='demo')
        other=Session();reply=await self.pipeline.turn(other,{'input':'你好'},lambda *a:None)
        self.assertFalse(reply['next_step']['slots']);self.assertIsNone(other.result)

    async def test_valid_model_patch_is_used(self):
        await self.run_turn(action='demo')
        decision={'intent':'update','slots':{'budget':130},'message':'','plan_id':None}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(decision)}}]}))) as client:
            self.pipeline.runner.client=client;self.pipeline.runner.key='fake'
            reply=await self.run_turn(input='把人均控制在一百三')
        self.assertEqual(reply['intent'],'plan');self.assertEqual(len(reply['next_step']['result']['plans']),1)

    async def test_live_places_have_sources_but_no_verified_exhibition_claim(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(live_handler)) as client:
            self.pipeline.places.client=client;self.pipeline.places.key='fake'
            reply=await self.run_turn(request=demo_request())
        result=reply['next_step']['result'];self.assertEqual(result['meta']['mode'],'live')
        self.assertEqual(len(result['plans']),3)
        self.assertTrue(all(s['source']['verification']=='poi_verified' and s['source']['url'] for p in result['plans'] for s in p['stops']))
        self.assertIn('未核实',result['meta']['dataNote'])

    async def test_activity_links_and_unsafe_url_filter(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(live_handler)) as client:
            self.pipeline.places.client=client;self.pipeline.places.key='fake'
            self.pipeline.activities.client=client;self.pipeline.activities.key='fake'
            result=(await self.run_turn(request=demo_request()))['next_step']['result']
        self.assertEqual(result['meta']['activitySearch']['status'],'completed')
        for p in result['plans']:
            exhibition=next(s for s in p['stops'] if s['category']=='看展')
            self.assertEqual(len(exhibition['activitySources']),1)
            self.assertEqual(exhibition['activitySources'][0]['verification'],'search_result')

    async def test_activity_outage_keeps_map_result(self):
        def handler(request):return httpx.Response(500) if request.url.host=='api.tavily.com' else live_handler(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.pipeline.places.client=client;self.pipeline.places.key='fake'
            self.pipeline.activities.client=client;self.pipeline.activities.key='fake'
            result=(await self.run_turn(request=demo_request()))['next_step']['result']
        self.assertEqual(result['meta']['mode'],'live');self.assertEqual(result['meta']['activitySearch']['status'],'unavailable')
        self.assertTrue(result['meta']['warnings'])

    async def test_http_protocol_and_input_errors(self):
        app=build_app(pipeline=self.pipeline,mount_gradio=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            self.assertEqual((await client.post('/turn',content='{bad')).status_code,400)
            self.assertEqual((await client.post('/turn',json={'input':'x'*2100})).status_code,400)
            self.assertEqual((await client.post('/turn',content='x'*20001)).status_code,413)
            response=await client.post('/turn',json={'action':'demo'})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            self.assertEqual(events[-1]['data']['intent'],'plan');self.assertTrue(events[-1]['conversation'])
            self.assertTrue(any(e['type']=='progress' for e in events))
            conversation=events[-1]['conversation']
            response=await client.post('/turn',json={'conversation':conversation,'input':'预算改成130元'})
            last=json.loads([l for l in response.text.splitlines() if l.startswith('data: ')][-1][6:])
            self.assertEqual(len(last['data']['next_step']['result']['plans']),1)

class DomainTests(unittest.TestCase):
    def test_total_budget_is_divided_across_confirmed_people(self):
        req=demo_request()
        self.assertEqual(extract('总预算300元',req)['slots']['budget'],150)
        decision=extract('我们三个人，总预算300.01元',req)
        self.assertEqual(decision['slots']['budget'],100)
        self.assertEqual(len(decision['slots']['people']),3)
        self.assertNotIn('budget',extract('总预算300元',{})['slots'])
        self.assertIn('同行人数',extract('总预算300元',{})['message'])

    def test_personal_and_shared_time_windows(self):
        req=demo_request()
        patch_=extract('我12:00到18:00有空，朋友14:00到20:00有空',req)['slots']
        self.assertEqual(normalize({**req,**patch_})['commonWindow'],{'from':'14:00','to':'18:00'})
        self.assertEqual(patch_['people'][0]['availableTo'],'18:00')
        self.assertEqual(patch_['people'][1]['availableTo'],'20:00')
        friend=extract('朋友14:00到20:00有空',req)['slots']['people']
        self.assertEqual(friend[0],req['people'][0]);self.assertEqual(friend[1]['availableFrom'],'14:00')
        shared=extract('都在13:00到19:00有空',req)['slots']['people']
        self.assertTrue(all(p['availableFrom']=='13:00' and p['availableTo']=='19:00' for p in shared))

    def test_named_windows_and_unassigned_windows(self):
        req=demo_request();req['people'][1]['name']='小林'
        people=extract('我12:00到18:00，小林14:00到20:00',req)['slots']['people']
        self.assertEqual(people[1]['availableFrom'],'14:00')
        decision=extract('12:00到18:00，14:00到20:00',req)
        self.assertIn('对应到每个人',decision['message'])
        self.assertTrue(all(p['availableFrom']==p['availableTo']=='' for p in decision['slots']['people']))

    def test_negated_transactions_do_not_reject_planning(self):
        for text in ('不需要订票，只想看展','不要帮我订票并付款，只排路线','不订票，想喝咖啡'):
            with self.subTest(text=text):self.assertEqual(extract(text,demo_request())['intent'],'update')
        self.assertEqual(extract('不需要订票，但帮我付款',demo_request())['intent'],'out_of_scope')

    def test_specific_sunday_precedes_generic_weekend(self):
        from datetime import date
        for text in ('周日想看展，周末都可以','这周末想看展，周日出发','下周日想看展'):
            with self.subTest(text=text):self.assertEqual(date.fromisoformat(extract(text,{})['slots']['date']).weekday(),6)

    def test_full_group_count_is_checked(self):
        for count in ('0','7','10','16','十','十二','二十','一百'):
            with self.subTest(count=count):self.assertEqual(extract(f'我们{count}个人，周六出发',demo_request())['intent'],'out_of_scope')
        self.assertEqual(len(extract('我们6个人',{})['slots']['people']),6)

    def test_bad_date_and_negative_budget(self):
        for patch_ in ({'date':'2027-02-30'},{'budget':-1},{'budget':True},{'budget':float('nan')}):
            with self.assertRaises(PlanError):normalize({**demo_request(),**patch_})
    def test_single_origin_revision_preserves_other_person(self):
        req=demo_request();decision=extract('朋友改从龙翔桥出发',req)
        self.assertEqual(decision['slots']['people'][0]['location'],'武林广场')
        self.assertEqual(decision['slots']['people'][1]['location'],'龙翔桥')

def live_handler(request):
    if request.url.host=='api.tavily.com':
        query=json.loads(request.content)['query'];match=re.search(r'(?:美术馆|火锅|公园)\d',query);venue=match.group() if match else '美术馆0'
        return httpx.Response(200,json={'results':[{'title':'杭州 '+venue+' 活动参考','url':'https://example.org/'+venue,'content':'杭州 '+venue+' 展期待核实'},{'title':'杭州 '+venue,'url':'javascript:alert(1)','content':''}]})
    if 'geocode' in request.url.path:return httpx.Response(200,json={'status':'1','geocodes':[{'city':'杭州市','location':'120.164,30.275'}]})
    keyword=request.url.params['keywords'];lng,lat={'美术馆':(120.162,30.274),'火锅':(120.163,30.275),'公园':(120.164,30.276)}[keyword]
    return httpx.Response(200,json={'status':'1','pois':[{'name':f'{keyword}{i}','address':'测试地址','location':f'{lng+i*.001},{lat+i*.001}'} for i in range(3)]})

if __name__=='__main__':unittest.main()
