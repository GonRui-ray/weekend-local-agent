"""Bounded, source-preserving ingestion. Search snippets never become verified events."""
from __future__ import annotations
import asyncio
import copy
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from jsonschema import validate, FormatChecker
from weekend_agent.domain.planning import stamp

ROOT = Path(__file__).resolve().parents[2]

def safe_url(value):
    if not isinstance(value, str) or len(value) > 2048: return None
    try:
        p = urlparse(value)
        if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password: return None
        query = urlencode([(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith(('utm_', 'spm'))])
        return urlunparse((p.scheme, p.netloc.lower(), p.path or '/', p.params, query, ''))
    except ValueError: return None

def kind_for(url):
    host = (urlparse(url).hostname or '').lower()
    if host.endswith('.gov.cn'): return 'official'
    for domain in ('xiaohongshu.com', 'xhslink.com'):
        if host == domain or host.endswith('.'+domain): return 'social'
    for domain in ('damai.cn', 'douban.com', 'dianping.com', 'meituan.com', 'maoyan.com'):
        if host == domain or host.endswith('.'+domain): return 'local_life'
    return 'web'

def same_venue(venue, name):
    clean = lambda s: re.sub(r'[\s·（）()\-]', '', s)
    return clean(venue) == clean(name)

class Activities:
    def __init__(self, client, key='', *, feed_url='', feed_token='', feed_path=''):
        self.client, self.key = client, key
        self.feed_url, self.feed_token, self.feed_path = feed_url, feed_token, feed_path
        self.feed_schema = json.loads((ROOT/'event-feed.schema.json').read_text())
        self.limit = asyncio.Semaphore(4)

    async def _search(self, query, req):
        async with self.limit:
            res = await self.client.post('https://api.tavily.com/search', headers={'Authorization':'Bearer '+self.key}, json={'query':query,'search_depth':'basic','max_results':4,'include_answer':False}, timeout=10)
            res.raise_for_status()
            if len(res.content)>2_000_000: raise ValueError('response_too_large')
            data = res.json()
        sources = []
        for record in data.get('results', [])[:4]:
            if not isinstance(record, dict): continue
            url = safe_url(record.get('url'))
            title, snippet = str(record.get('title', '活动来源'))[:160], str(record.get('content', ''))[:600]
            # Do not mix nearby cities, unrelated generic results or undated snippets with records.
            if not url or req['city'] not in title+snippet: continue
            sources.append({'title':title,'url':url,'snippet':snippet,'checkedAt':stamp(),'verification':'search_result','kind':kind_for(url),'publisher':urlparse(url).hostname or '', 'publishedAt':None,'eventStart':None,'eventEnd':None})
        return list({s["url"]:s for s in sources}.values())

    async def discover(self, req, emit):
        context = {'events':[], 'sources':[], 'channels':[], 'rejectedRecords':0, 'warnings':[]}
        emit('information','running',f"正在汇集{req['city']}的本地生活信息",'按居住城市、出行日期和兴趣筛选，保留来源')
        if self.feed_url or self.feed_path:
            try:
                if self.feed_url:
                    if not safe_url(self.feed_url) or urlparse(self.feed_url).scheme != 'https': raise ValueError('https_required')
                    headers = {'Authorization':'Bearer '+self.feed_token} if self.feed_token else {}
                    res = await self.client.get(self.feed_url, headers=headers, timeout=10)
                    res.raise_for_status()
                    if len(res.content)>2_000_000: raise ValueError('feed_too_large')
                    data = res.json()
                else:
                    path = Path(self.feed_path)
                    if path.stat().st_size>2_000_000: raise ValueError('feed_too_large')
                    data = json.loads(path.read_text())
                records = data['events']
                if not isinstance(records, list) or len(records)>500: raise ValueError('invalid_feed')
                ids = set()
                for event in records:
                    try:
                        validate(event, self.feed_schema['properties']['events']['items'], format_checker=FormatChecker())
                        start, end, day = (date.fromisoformat(d) for d in (event['startsOn'],event['endsOn'],req['date']))
                        url = safe_url(event['url'])
                        if not url or start>end: raise ValueError('invalid_event')
                        if event.get('publishedAt'):
                            published = datetime.fromisoformat(event['publishedAt'].replace('Z','+00:00'))
                            if published.tzinfo is None or published > datetime.now(timezone.utc): raise ValueError('invalid_publication')
                        if event['city'].removesuffix('市') != req['city'] or not start<=day<=end: continue
                        key = event['id']
                        if key in ids: continue
                        ids.add(key); context['events'].append({**event,'url':url})
                    except Exception: context['rejectedRecords'] += 1
                context['channels'].append({'id':'feed','label':'授权 / 人工核对活动库','status':'completed' if context['events'] else 'empty','count':len(context['events'])})
            except Exception:
                context['channels'].append({'id':'feed','label':'授权 / 人工核对活动库','status':'unavailable','count':0})
                context['warnings'].append('活动供给接口暂时不可用，本轮不会将它标为已接入成功。')
        else: context['channels'].append({'id':'feed','label':'授权 / 人工核对活动库','status':'not_configured','count':0})
        channels = [('official','官方文化活动','site:gov.cn 展览 文化活动'),('local_life','本地生活公开资讯','(site:damai.cn OR site:douban.com OR site:dianping.com OR site:meituan.com) 周末'),('social','小红书公开搜索线索','site:xiaohongshu.com 周末 本地生活')]
        if self.key:
            async def search_channel(channel):
                id_, label, query = channel
                try:
                    records = await self._search(f"{req['city']} {req['residentArea']} {req['date']} {' '.join(req['preferences'])} {query}",req)
                    # A domain query is a hint to the search engine, not proof of provenance.
                    records = [r for r in records if r['kind']==id_]
                    return {'id':id_,'label':label,'status':'completed' if records else 'empty','count':len(records)},records
                except Exception: return {'id':id_,'label':label,'status':'unavailable','count':0},[]
            for channel,records in await asyncio.gather(*(search_channel(c) for c in channels)):
                context['channels'].append(channel);context['sources'].extend(records)
        else:
            context['channels'].extend({'id':id_,'label':label,'status':'not_configured','count':0} for id_,label,_ in channels)
        return context

    async def attach(self, candidates, req, emit, context=None):
        context = context or await self.discover(req,emit)
        # Work on at most 9 venues, including restaurants/cafes, before ranking routes.
        venues = list(dict.fromkeys(s['name'] for p in candidates for s in p['stops']))[:9]
        by_venue = {}
        for venue in venues:
            sources = []
            for event in context['events']:
                if same_venue(event['venue'], venue):
                    sources.append({'title':event['title'],'url':event['url'],'snippet':event.get('summary','')[:600],'checkedAt':stamp(),'verification':'provider_record','kind':event['kind'],'publisher':event['publisher'],'publishedAt':event.get('publishedAt'),'eventStart':event['startsOn'],'eventEnd':event['endsOn']})
            sources.extend(s for s in context['sources'] if venue in s['title']+s['snippet'])
            by_venue[venue] = sources
        successes, failed = 0, 0
        if self.key and venues:
            emit('information','running','正在对齐活动、店铺与地点','检索场馆和餐饮资讯；网页摘要不会自动变成已核实展期')
            async def search(venue):
                try:
                    sources = await self._search(f"{req['city']} {venue} {req['date']} 周末 活动 营业时间 价格 官方",req)
                    return venue, [s for s in sources if venue in s['title']+s['snippet']], False
                except Exception: return venue, [], True
            for venue,sources,error in await asyncio.gather(*(search(v) for v in venues)):
                failed += int(error);successes += int(not error);by_venue[venue].extend(sources)
        matched_venues = set()
        for candidate in candidates:
            for stop in candidate['stops']:
                unique = {}
                for s in by_venue.get(stop['name'], []):
                    if s['url'] not in unique: unique[s['url']] = s
                stop['activitySources'] = copy.deepcopy(list(unique.values())[:5])
                records = [e for e in context['events'] if same_venue(e['venue'],stop['name'])]
                prices = {e['price'] for e in records if e.get('price') is not None}
                if len(prices)==1: stop['cost'] = prices.pop()
                if len(prices)>1:
                    stop['cost'] = max(stop['cost'], max(prices))
                    context['warnings'].append(f"{stop['name']}的来源票价有冲突，按较高金额预留并请出发前核对。")
                if records:
                    stop['description'] = '；'.join(e['title'] for e in records[:2])+ '。来源标注的日期覆盖出行日，具体场次、余票和营业仍需确认。'
                if stop['activitySources']: matched_venues.add(stop['name'])
        matched = len(matched_venues)
        configured = bool(self.key or self.feed_path or self.feed_url)
        unavailable = any(c['status']=='unavailable' for c in context['channels']) or bool(failed)
        status = 'not_configured' if not configured else 'partial' if matched and unavailable else 'completed' if matched else 'unavailable' if unavailable else 'partial'
        warnings = list(dict.fromkeys(context['warnings']))
        if not matched: warnings.append('未取得与本轮地点相匹配的活动资料；当前仅为周边地点路线，不声称当天有活动。')
        if any(c['status'] in ('unavailable','empty') for c in context['channels'] if c['id']!='feed'): warnings.append('部分资讯渠道无匹配或暂不可用，来源状态已逐项展示。')
        emit('information','done' if matched else 'warning',f'已整合 {matched} 个地点的资讯' if matched else '本轮未取得匹配的活动资讯','已去重，排除异城、过期和无效记录；推荐前仍需核对场次与价格')
        provider = 'mixed' if self.key and (self.feed_path or self.feed_url) else 'tavily' if self.key else 'feed' if self.feed_path or self.feed_url else None
        return {'status':status,'provider':provider,'checkedAt':stamp() if configured else None},warnings,{'channels':context['channels'],'rejectedRecords':context['rejectedRecords']}
