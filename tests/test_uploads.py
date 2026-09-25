import json
from types import SimpleNamespace

import pytest

from autoapply.uploads import UploadTracker, operation


class Request:
    method = 'POST'
    url = 'https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSetFormValueToFile&token=SECRET'
    post_data_json = {  # noqa: RUF012 -- immutable fixture use
        'operationName':'ApiSetFormValueToFile', 'variables':{'secret':'PRIVATE'}}
    failure = 'net::ERR_CONNECTION_RESET'


def ui(**changes):
    return dict(attached=True, busy=False, disabled=False, warning=False,
                replace_visible=True, replace_usable=True, file_entry=True, **changes)


async def response(tracker, request, status=200, payload=None):
    async def body():
        return {'data':{'setFormValueToFile':{'id':'PRIVATE'}}} if payload is None else payload
    await tracker.response(SimpleNamespace(request=request, status=status, json=body))


async def complete():
    tracker = UploadTracker()
    tracker.select()
    request = Request()
    tracker.started(request)
    await response(tracker, request)
    tracker.finished(request)
    return tracker, request


async def test_lifecycle_requires_completion_metadata_and_clear_ui():
    t = UploadTracker(); t.select(); r = Request()
    assert t.verdict(ui(), ashby=True, expired=True)['category'] == 'UPLOAD_NOT_STARTED'
    t.started(r)
    assert t.verdict(ui(), ashby=True)['active']
    assert not t.result['ready']
    await response(t, r)
    assert not t.verdict(ui(), ashby=True)['ready']  # body/request not finished
    t.finished(r)
    busy = ui(); busy['busy'] = True
    assert not t.verdict(busy, ashby=True)['ready']
    assert t.verdict(ui(), ashby=True)['ready']
    assert t.result['metadata_confirmed']
    assert all(r['started_at'] and r['ended_at'] for r in t.result['requests'])
    assert {'UPLOAD_FILE_SELECTED','UPLOAD_REQUEST_STARTED','UPLOAD_REQUEST_OPERATION',
            'UPLOAD_REQUEST_COMPLETED','UPLOAD_RESPONSE_STATUS','UPLOAD_METADATA_CONFIRMED'} <= {e['kind'] for e in t.events}


@pytest.mark.parametrize('status,payload', [(500,{}), (200,{'errors':[{'message':'PRIVATE'}]}),
                                         (200,{'data':{'setFormValueToFile':{'success':False}}})])
async def test_failure_provenance(status,payload):
    t=UploadTracker(); t.select(); r=Request(); t.started(r)
    await response(t,r,status,payload); t.finished(r)
    assert t.verdict(ui(),ashby=True)['category']=='UPLOAD_FAILED'
    assert t.result['requests'][0]['status']==status
    assert 'PRIVATE' not in json.dumps(t.result)


def test_transport_failure_and_stall_distinct():
    t=UploadTracker();t.select();r=Request();t.started(r)
    assert t.verdict(ui(),ashby=True,expired=True)['category']=='UPLOAD_STALLED'
    t.failed(r)
    assert t.verdict(ui(),ashby=True)['category']=='UPLOAD_FAILED'
    assert t.result['requests'][0]['failure_code']=='net::ERR_CONNECTION_RESET'
    assert not t.result['requests'][0]['completed']


@pytest.mark.parametrize('key,value', [('attached',False),('busy',True),('disabled',True),
    ('warning',True),('replace_usable',False),('file_entry',False)])
async def test_ui_conditions_block_ready(key,value):
    t,_=await complete();state=ui();state[key]=value
    assert not t.verdict(state,ashby=True)['ready']
    if key=='attached':
        assert t.result['category']=='UPLOAD_FAILED'


async def test_metadata_missing_and_second_upload_block_ready():
    t,r=await complete()
    t.requests[r]['metadata_confirmed']=False
    assert not t.verdict(ui(),ashby=True)['ready']
    t.requests[r]['metadata_confirmed']=True
    t.select()
    assert not t.verdict(ui(),ashby=True)['ready']


async def test_null_metadata_is_not_confirmation():
    t=UploadTracker();t.select();r=Request();t.started(r)
    await response(t,r,payload={'data':{'setFormValueToFile':None}});t.finished(r)
    assert not t.verdict(ui(),ashby=True)['ready']


async def test_only_safe_metadata_retained():
    t,_=await complete()
    dump=json.dumps(t.events)+json.dumps(t.verdict(ui(),ashby=True))
    assert all(s not in dump for s in ['SECRET','PRIVATE','variables','token='])
    r=Request();r.url='https://jobs.ashbyhq.com/api/non-user-graphql'
    assert operation(r)=='ApiSetFormValueToFile'
    r.url='https://storage.test/upload/PRIVATE?token=SECRET'
    t.started(r)
    assert t.requests[r]['path']=='/[upload-path]'


@pytest.mark.parametrize('status', [204, 500])
async def test_greenhouse_requires_completed_storage_upload(status):
    t = UploadTracker(); t.greenhouse = True; t.select()
    assert not t.verdict(ui(), ashby=False)['ready']
    r = Request(); r.url = 'https://boards-production.s3.us-east-1.amazonaws.com/'
    t.started(r)
    assert t.requests[r]['operation'] == 'GreenhouseS3Upload'
    await response(t, r, status)
    assert not t.verdict(ui(), ashby=False)['ready']
    t.finished(r)
    assert t.verdict(ui(), ashby=False)['ready'] is (status == 204)
