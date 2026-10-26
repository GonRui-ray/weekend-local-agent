import copy
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'));sys.path.insert(0,str(ROOT/'space'))
from weekend_agent.server import create_pipeline
from weekend_agent.service.pipeline import Session
from weekend_agent.domain.understanding import demo_request,extract
from weekend_agent.domain.planning import normalize,schedule
from weekend_agent.adapters.information import safe_url
from app import build_app

class ResidentInformationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env=patch.dict(os.environ,{'MODEL_API_KEY':'','AMAP_KEY':'','TAVILY_KEY':'','EVENT_FEED_URL':'','EVENT_FEED_PATH':'','EVENT_FEED_TOKEN':''});self.env.start()
        self.events=[];self.calls=[]
        self.client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.pipeline,_=create_pipeline(self.client);self.pipeline.places.key='test'
        self.session=Session();self.feed=[];self.fail_search=False;self.fail_feed=False
        self.req={**demo_request(),'city':'上海','residentArea':'静安区','people':[{'name':'我','location':'日常地铁站','availableFrom':'12:00','availableTo':'20:00'}]}
    async def asyncTearDown(self):await self.client.aclose();self.env.stop()
    def event(self,**patch_):
        day=self.req['date']
        return {'id':'one','city':'上海','title':'邻里小展','venue':'新空间','category':'看展','startsOn':day,'endsOn':day,'url':'https://culture.example.test/current','kind':'official','publisher':'活动供给测试','price':30,**patch_}
    def handle(self,request):
        self.calls.append(request)
        if request.url.host=='feed.example.test':return httpx.Response(500) if self.fail_feed else httpx.Response(200,json={'events':self.feed})
        if request.url.host=='api.tavily.com':
            if self.fail_search:return httpx.Response(500)
            query=json.loads(request.content)['query'];venue=next((x for x in query.split() if re.match(r'美术馆\d$|火锅\d$|公园\d$|新空间$',x)), '美术馆0')
            return httpx.Response(200,json={'results':[
                {'title':'上海 '+venue+' 活动','url':'https://culture.gov.cn/event?utm_source=test','content':'上海 '+venue+' 活动摘要，日期尚待核对'},
                {'title':'上海 '+venue+' 活动','url':'https://culture.gov.cn/event','content':'上海 '+venue+' 重复'},
                {'title':'上海 '+venue+' 探店','url':'https://www.xiaohongshu.com/explore/record','content':'上海 '+venue+' 个人体验'},
                {'title':'北京 '+venue,'url':'https://example.test/wrong-city','content':'北京活动'},
                {'title':'上海 '+venue,'url':'javascript:alert(1)','content':'上海 '+venue} ]})
        path=request.url.path
        if path.endswith('/coordinate/convert'):return httpx.Response(200,json={'status':'1','locations':'121.451,31.221'})
        if path.endswith('/regeo'):return httpx.Response(200,json={'status':'1','regeocode':{'addressComponent':{'city':'上海市','district':'静安区'}}})
        if '/geocode/' in path:return httpx.Response(200,json={'status':'1','geocodes':[{'city':request.url.params['city']+'市','location':'121.45,31.22'}]})
        if path.endswith('/place/text'):return httpx.Response(200,json={'status':'1','pois':[{'name':request.url.params['keywords'],'cityname':'上海市','address':'常住片区附近','location':'121.4505,31.2205'}]})
        keyword=request.url.params['keywords'];base={'美术馆':0,'火锅':.0002,'公园':.0004}[keyword]
        return httpx.Response(200,json={'status':'1','pois':[{'name':keyword+str(i),'cityname':'上海市','address':'街区测试地址','location':f'{121.45+base+i*.0001},{31.22+base+i*.0001}'} for i in range(3)]})
    async def turn(self,**payload):return await self.pipeline.turn(self.session,payload,lambda *x:self.events.append(x))
    def connect_feed(self):self.pipeline.activities.feed_url='https://feed.example.test/events'

    async def test_unconfigured_city_never_uses_hangzhou(self):
        self.pipeline.places.key=''
        reply=await self.turn(request=self.req)
        self.assertEqual(reply['intent'],'error');self.assertIsNone(reply['next_step']['result']);self.assertEqual(reply['next_step']['slots']['city'],'上海');self.assertNotIn('杭州',reply['message'])
    async def test_home_switch_clears_stale_origins_and_routes(self):
        await self.turn(action='demo')
        reply=await self.turn(home={'city':'成都','residentArea':'玉林'},input='想喝咖啡')
        self.assertIsNone(reply['next_step']['result']);self.assertNotIn('people',reply['next_step']['slots']);self.assertEqual(reply['next_step']['slots']['city'],'成都');self.assertFalse(self.session.sample)
    async def test_multicity_uses_confirmed_city_on_map_requests(self):
        reply=await self.turn(request=self.req)
        result=reply['next_step']['result'];self.assertEqual(result['request']['city'],'上海');self.assertEqual(result['request']['audience'],'resident')
        self.assertTrue(all(r.url.params['city']=='上海' for r in self.calls if '/geocode/' in r.url.path))
        self.assertTrue(all('上海' in stop['address'] for p in result['plans'] for stop in p['stops']))
    async def test_feed_changes_actual_anchor_and_budget_before_selection(self):
        self.connect_feed();self.feed=[self.event()];self.req['budget']=150
        reply=await self.turn(request=self.req);result=reply['next_step']['result']
        self.assertEqual(reply['intent'],'plan');self.assertEqual(result['plans'][0]['meetingPoint'],'新空间')
        stop=result['plans'][0]['stops'][0]
        self.assertEqual(stop['costEstimate'],30);self.assertEqual(stop['activitySources'][0]['verification'],'provider_record');self.assertLessEqual(result['plans'][0]['budgetPerPerson']['total'],150)
    async def test_feed_price_can_make_event_infeasible(self):
        self.connect_feed();self.feed=[self.event(price=190)];self.req['budget']=180
        result=(await self.turn(request=self.req))['next_step']['result']
        self.assertTrue(all(p['meetingPoint']!='新空间' for p in result['plans']))
    async def test_expired_other_city_duplicate_and_invalid_feed_records_filtered(self):
        self.connect_feed();self.feed=[self.event(),self.event(),self.event(id='other',city='北京'),self.event(id='old',startsOn='2020-01-01',endsOn='2020-01-02'),self.event(id='bad',endsOn='bad-date')]
        result=(await self.turn(request=self.req))['next_step']['result'];info=result['meta']['information']
        self.assertEqual(info['channels'][0]['count'],1);self.assertEqual(info['rejectedRecords'],1)
        self.assertEqual(len(result['plans'][0]['stops'][0]['activitySources']),1)
    async def test_conflicting_prices_do_not_become_a_claimed_ticket_price(self):
        self.connect_feed();self.feed=[self.event(venue='美术馆0',price=10),self.event(id='two',venue='美术馆0',price=120,url='https://other.example.test/event')]
        result=(await self.turn(request=self.req))['next_step']['result']
        self.assertTrue(any('冲突' in w for w in result['meta']['warnings']))
        # Conflicting supply prices cannot silently select the cheapest admission.
        self.assertNotEqual(result['plans'][0]['stops'][0]['costEstimate'],10)
    async def test_public_sources_stay_unverified_and_are_deduplicated(self):
        self.pipeline.activities.key='test'
        result=(await self.turn(request=self.req))['next_step']['result']
        sources=result['plans'][0]['stops'][0]['activitySources']
        self.assertEqual(len(sources),2);self.assertEqual({s['kind'] for s in sources},{'official','social'})
        self.assertTrue(all(s['verification']=='search_result' and s['eventStart'] is None for s in sources));self.assertTrue(all('wrong-city' not in s['url'] for s in sources))
        self.assertLessEqual(len([r for r in self.calls if r.url.host=='api.tavily.com']),12)
        self.assertFalse(any('日常地铁站' in json.loads(r.content)['query'] for r in self.calls if r.url.host=='api.tavily.com'))
    async def test_search_and_feed_outage_preserve_map_only_result(self):
        self.pipeline.activities.key='test';self.connect_feed();self.fail_search=self.fail_feed=True
        result=(await self.turn(request=self.req))['next_step']['result']
        self.assertEqual(result['meta']['mode'],'live');self.assertEqual(result['meta']['activitySearch']['status'],'unavailable')
        self.assertTrue(all(not s['activitySources'] for p in result['plans'] for s in p['stops']))
    async def test_avoid_places_filters_route_content(self):
        self.req['avoidPlaces']=['美术馆0','公园0']
        result=(await self.turn(request=self.req))['next_step']['result']
        self.assertTrue(all(not any(a in s['name'] for a in self.req['avoidPlaces']) for p in result['plans'] for s in p['stops']))
    async def test_location_converts_gps_and_requires_confirmation(self):
        app=build_app(pipeline=self.pipeline,mount_gradio=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            response=await client.post('/api/location',json={'longitude':121.45,'latitude':31.22})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json(),{'city':'上海','district':'静安区','needsConfirmation':True})
            self.assertTrue(any('/coordinate/convert' in r.url.path for r in self.calls))
            self.assertEqual((await client.post('/api/location',json={'longitude':'bad','latitude':31})).status_code,400)
            self.assertEqual((await client.post('/api/location',json={'longitude':1,'latitude':2})).status_code,503)
    async def test_pages_cors_accepts_only_configured_origin(self):
        with patch.dict(os.environ,{'CORS_ORIGINS':'https://alice.github.io'}):
            app=build_app(pipeline=self.pipeline,mount_gradio=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            response=await client.options('/turn',headers={'Origin':'https://alice.github.io','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type'})
            self.assertEqual(response.status_code,200);self.assertEqual(response.headers.get('access-control-allow-origin'),'https://alice.github.io')
            response=await client.options('/turn',headers={'Origin':'https://other.github.io','Access-Control-Request-Method':'POST'})
            self.assertEqual(response.status_code,400);self.assertNotIn('access-control-allow-origin',response.headers)

    async def test_cloud_readiness_does_not_hide_missing_services(self):
        app=build_app(pipeline=self.pipeline,mount_gradio=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            response=await client.get('/ready');self.assertEqual(response.status_code,503);self.assertFalse(response.json()['ready'])
            self.assertEqual((await client.get('/health')).status_code,200)
        self.assertNotIn('test',(response.text))

class ResidentRulesTests(unittest.TestCase):
    def test_commute_limit_is_enforced(self):
        req=normalize(demo_request());req['maxCommuteMinutes']=30
        from weekend_agent.adapters.providers import Places
        places=Places(None);people,candidates=places.demo(req)
        for p in people:p['coordinate']={'lng':121.45,'lat':31.22}
        self.assertIsNone(schedule(candidates[0],req,people,'demo'))
    def test_resident_parser_understands_other_city_and_exclusions(self):
        decision=extract('我住在青岛，单程最多30分钟，不想再去栈桥、五四广场',{})
        self.assertEqual(decision['slots']['city'],'青岛');self.assertEqual(decision['slots']['maxCommuteMinutes'],30);self.assertEqual(decision['slots']['avoidPlaces'],['栈桥','五四广场'])
    def test_source_url_cannot_spoof_credentials_or_scripts(self):
        self.assertIsNone(safe_url('https://secret@example.test/'))
        self.assertIsNone(safe_url('javascript:alert(1)'))
        self.assertEqual(safe_url('https://example.test/e?utm_source=x#fragment'),'https://example.test/e')
