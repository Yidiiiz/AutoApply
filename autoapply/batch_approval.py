"""Destination-specific authorization for the explicitly approved fill-only batch."""
import hashlib
import json
import re
from urllib.parse import urlsplit
from .archive import atomic_json
from .models import now


def is_final_submission(url, method, body=None):
    if method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return False
    return bool(re.search(r'(?:submit|finalize)(?:application)?', urlsplit(url).path, re.I)
                or isinstance(body, str) and re.search(r'"(?:operationName|action)"\s*:\s*"[^\"]*(?:submit|finalize)', body, re.I))

PAYLOADS = [
 'resume.pdf', 'first and last name', 'email address', 'phone number',
 'current city/location', 'university/school', 'degree', 'major', 'graduation date',
 'LinkedIn', 'GitHub', 'portfolio/personal website', 'U.S. work authorization',
 'current sponsorship requirement', 'future sponsorship requirement',
 'saved eligibility answers', 'saved work-location preferences', 'saved technical-interest preferences',
 'saved prior-employer disclosures', 'saved conflict-of-interest disclosures',
 'saved government-connection disclosures', 'saved SMS recruiting preference',
 'saved applicant/privacy-policy acknowledgements',
 'saved demographic/self-identification answers only for corresponding employer fields',
 'semantically equivalent previously verified application answers',
 'grounded narratives derived only from verified profile, resume, projects, research and saved experience']

class ApprovedDestinations:
 def __init__(self,path):
  raw=path.read_bytes()
  self.source_sha256=hashlib.sha256(raw).hexdigest()
  source=json.loads(raw)
  self.destinations={d['id']:{**d,'domain':urlsplit(d['canonical_url']).hostname} for d in source['destinations']}
  if len(self.destinations)!=5 or any(urlsplit(d['canonical_url']).scheme!='https' for d in self.destinations.values()):
   raise ValueError('Approval must enumerate exactly five HTTPS employer destinations')
  self.current=None
  self.blocked=[]
  self.final_requests_blocked=0

 def record(self,path,owner_id):
  atomic_json(path,{'recorded_at':now(),'authorization':'Explicit destination-specific user approval in current conversation',
   'source':'data/private/fill-only-network-approval.json','source_sha256':self.source_sha256,
   'destinations':list(self.destinations.values()),'employer_payload_categories':PAYLOADS,
   'discord':{'recipient':'Already configured Discord owner only','recipient_sha256':hashlib.sha256(owner_id.encode()).hexdigest(),
    'domains':['discord.com','gateway.discord.gg'],'allowed':['application ID','employer','role','exact unknown question','available choices','concise reason input required'],
    'forbidden':['resume','full profile','demographic answers','authentication secrets','cookies','unrelated private application data']},
   'restrictions':{'auto_submit':False,'final_submit_clicks':0,'final_submission_requests':0,'maximum_active_application_tabs':1,
    'captcha_bypass':False,'external_AI_providers':False,'unrelated_domains':False},
   'execution':'Single live smoke first; continue sequentially only after READY_FOR_MANUAL_SUBMIT; at most five'})

 def select(self,app):
  destination=self.destinations.get(app['id'])
  if not destination or any(app[k]!=destination[k] for k in ('company','canonical_url')):
   raise RuntimeError('APPROVED_DESTINATION_MISMATCH')
  self.current=destination

 def decision(self,url,method,*,navigation=False,body=None):
  parsed=urlsplit(url)
  if not self.current or parsed.scheme!='https' or parsed.hostname!=self.current['domain']:
   return 'UNAPPROVED_DOMAIN'
  if is_final_submission(url, method, body):
   return 'FINAL_SUBMISSION_FORBIDDEN'
  if navigation:
   approved=urlsplit(self.current['canonical_url']).path.rstrip('/')
   if parsed.path.rstrip('/')!=approved and not parsed.path.startswith(approved+'/'):
    return 'UNAPPROVED_APPLICATION_NAVIGATION'
  return None

 async def route(self,route):
  request=route.request
  reason=self.decision(request.url,request.method,navigation=request.is_navigation_request() and request.frame==request.frame.page.main_frame,
                       body=request.post_data if request.method not in {'GET','HEAD','OPTIONS'} else None)
  if reason:
   parsed=urlsplit(request.url)
   self.blocked.append({'domain':parsed.hostname,'method':request.method,'reason':reason})
   if reason=='FINAL_SUBMISSION_FORBIDDEN':self.final_requests_blocked+=1
   await route.abort('blockedbyclient')
  else:
   await route.continue_()
