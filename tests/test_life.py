import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
from PIL import Image
import pytest
from maibot_sdk import PluginPaths

from qq_life_lite.config import LifeConfig
from qq_life_lite.media import normalize_image
from qq_life_lite.plugin import QQLifePlugin
from qq_life_lite.qzone import Qzone, RemoteFailure, UncertainResult, decode_response
from qq_life_lite.store import Store


def run(coro):
    return asyncio.run(coro)


def png():
    buffer = BytesIO()
    Image.new('RGB', (32, 32), (0, 90, 200)).save(buffer, format='PNG')
    return buffer.getvalue()


def plugin(tmp_path):
    p = QQLifePlugin()
    cfg = LifeConfig()
    cfg.life.bot_uin = '123456'
    cfg.life.owner_ids = ['999999']
    cfg.life.source_groups = ['111111']
    p.set_plugin_config(cfg.model_dump())
    p.store = Store(tmp_path)
    p._set_context(SimpleNamespace(
        paths=PluginPaths(data_dir=tmp_path, runtime_dir=tmp_path / 'runtime'),
        chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=[{
            'session_id': 'group-session', 'group_id': '111111', 'account_id': '123456', 'platform': 'qq',
        }])),
        message=SimpleNamespace(get_by_id=AsyncMock(return_value={
            'message_id': 'msg1', 'session_id': 'group-session', 'raw_message': [{'type': 'image', 'binary_data_base64': base64.b64encode(png()).decode()}],
        }), get_recent=AsyncMock(return_value=[])),
        api=SimpleNamespace(call=AsyncMock()), send=SimpleNamespace(text=AsyncMock()),
        logger=Mock(), llm=SimpleNamespace(generate=AsyncMock()),
    ))
    return p


def test_config_and_sdk_components(tmp_path):
    p = plugin(tmp_path)
    assert p.cfg.mode == 'draft'
    names = {item['name'] for item in p.get_components()}
    assert {'qq_life_collect', 'qq_life_propose', 'qq_life_status', 'ql'} <= names
    p.store.close()


def test_collection_is_deduplicated_and_not_uploaded_in_draft(tmp_path):
    p = plugin(tmp_path)
    p.store.set('album_id', 'album-1')
    async def scenario():
        a = await p.collect('group-session', 'msg1', '可爱的蓝色小鱼')
        b = await p.collect('group-session', 'msg1', '同一张图')
        assert a['local_saved'] and not a['album_uploaded']
        assert a['image_id'] == b['image_id']
        assert len(p.store.images()) == 1 and len(p.store.jobs()) == 1
        p.ctx.api.call.assert_not_awaited()
    run(scenario())
    p.store.close()


def test_unapproved_session_cannot_collect(tmp_path):
    p = plugin(tmp_path)
    with pytest.raises(ValueError):
        run(p.collect('forged-session', 'msg1', 'x'))
    p.ctx.message.get_by_id.assert_not_awaited()
    p.store.close()


def test_owner_only_command(tmp_path):
    p = plugin(tmp_path)
    result = run(p.command(user_id='123', platform='qq', stream_id='group-session', matched_groups={'rest': 'mode auto'}))
    assert result[0] is False and p.mode() == 'draft'
    p.ctx.api.call.assert_not_awaited()
    p.store.close()


def test_post_draft_does_not_publish(tmp_path):
    p = plugin(tmp_path)
    a = run(p.propose('post', {'text': '今天捡到一张好看的蓝鱼图。'}))
    b = run(p.propose('post', {'text': '今天捡到一张好看的蓝鱼图。'}))
    assert a['id'] == b['id'] and a['state'] == 'draft'
    p.ctx.api.call.assert_not_awaited()
    p.store.close()


def test_unknown_submission_is_never_repeated(tmp_path):
    p = plugin(tmp_path)
    p.verify_account = AsyncMock()
    client = SimpleNamespace(authenticate=AsyncMock(), publish=AsyncMock(side_effect=UncertainResult('timeout')))
    p.qzone = lambda: client
    async def scenario():
        job = await p.propose('post', {'text': '你好空间'})
        result = await p.execute(job['id'])
        assert result['state'] == 'unknown'
        with pytest.raises(ValueError):
            await p.execute(job['id'])
        assert client.publish.await_count == 1
    run(scenario())
    p.store.close()


def test_restart_preserves_inflight_and_rate_limit(tmp_path):
    s = Store(tmp_path)
    a = s.queue('post', {'text': 'a'})
    s.claim(a['id'], 2, 3600)
    s.close()
    s = Store(tmp_path)
    assert s.job(a['id'])['state'] == 'unknown'
    b = s.queue('post', {'text': 'b'})
    with pytest.raises(ValueError, match='冷却'):
        s.claim(b['id'], 2, 3600)
    s.close()


def test_avatar_uses_only_collection(tmp_path):
    p = plugin(tmp_path)
    with pytest.raises(ValueError):
        run(p.propose('avatar', {'image_id': '/etc/passwd'}))
    p.store.close()


def test_image_limits_and_normalization():
    data = normalize_image(base64.b64encode(png()).decode(), 1)
    with Image.open(BytesIO(data)) as img:
        assert img.size == (32, 32)
    with pytest.raises(ValueError):
        normalize_image('A' * 2000000, 1)
    with pytest.raises(Exception):
        normalize_image(base64.b64encode(b'not an image').decode(), 1)


async def authenticated_qzone(handler):
    async def call(name, **kwargs):
        if name == 'system.get_login_info':
            return {'user_id': 123456}
        return {'status': 'ok', 'retcode': 0, 'data': {'cookies': 'uin=o123456; p_skey=test-key; skey=test-key2;'}}
    q = Qzone(call, '123456', httpx.MockTransport(handler))
    await q.authenticate()
    return q


def test_qzone_album_upload_uses_specified_album():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ret': 0, 'data': {'albumid': 'album-1', 'lloc': 'photo-1'}})
    async def scenario():
        q = await authenticated_qzone(handler)
        result = await q.upload(png(), 'album-1')
        assert result['albumid'] == 'album-1'
    run(scenario())
    body = requests[0].content.decode()
    assert 'albumtype=0' in body and 'albumid=album-1' in body and 'refer=album' in body


def test_album_wrong_id_is_unknown():
    async def scenario():
        q = await authenticated_qzone(lambda req: httpx.Response(200, json={'ret': 0, 'data': {'albumid': 'wrong', 'lloc': 'x'}}))
        with pytest.raises(UncertainResult):
            await q.upload(png(), 'wanted')
    run(scenario())


@pytest.mark.parametrize('body,expected', [({'code': 0, 'tid': 'real-tid'}, 'ok'), ({'code': 0}, 'unknown'), ({'code': -3000}, 'failed')])
def test_post_receipts(body, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body)
    async def scenario():
        q = await authenticated_qzone(handler)
        if expected == 'ok':
            assert (await q.publish('test', []))['tid'] == 'real-tid'
        else:
            with pytest.raises(UncertainResult if expected == 'unknown' else RemoteFailure):
                await q.publish('test', [])
    run(scenario())
    assert len(calls) == 1


def test_cookie_never_appears_in_error():
    async def scenario():
        q = await authenticated_qzone(lambda req: httpx.Response(403, text='secret-test-key'))
        with pytest.raises(UncertainResult) as result:
            await q.publish('test', [])
        assert 'secret-test-key' not in str(result.value)
    run(scenario())


def test_missing_cookie_blocks_without_http():
    call = AsyncMock(side_effect=[{'user_id': 123456}, {'cookies': ''}])
    q = Qzone(call, '123456')
    with pytest.raises(RemoteFailure, match='p_skey'):
        run(q.authenticate())


def test_jsonp_decoding():
    assert decode_response('_Callback({"code":0,"data":{}});')['code'] == 0


def test_group_cannot_read_private_collections(tmp_path):
    p = plugin(tmp_path)
    p.store.add_image(png(), 'private-session', 'secret-msg', '私聊秘密', 100)
    result = run(p.tool_status(stream_id='group-session'))
    assert result[0] and '私聊秘密' not in result[1]
    p.store.close()


def test_autocollect_waits_for_recognition_and_uses_sdk_data(tmp_path):
    p = plugin(tmp_path)
    p.quiet = lambda: False
    p.store.set('initialized:group-session', True)
    p.store.set('next_decision', 9999999999)
    item = {'message_id': 'msg1', 'raw_message': [{'type': 'image', 'data': '[图片，识别中.....]'}]}
    p.ctx.message.get_recent.return_value = [item]
    p.decide = AsyncMock(return_value={'id': 'msg1', 'reason': '蓝色小鱼很好看'})
    async def scenario():
        await p.tick()
        p.decide.assert_not_awaited()
        item['raw_message'][0]['data'] = '[图片：一条可爱蓝色卡通小鲸鱼]'
        await p.tick()
        assert len(p.store.images()) == 1
        await p.tick()
        assert p.decide.await_count == 1
    run(scenario())
    p.store.close()


def test_avatar_and_nickname_receipts(tmp_path):
    p = plugin(tmp_path)
    p.verify_account = AsyncMock()
    image = p.store.add_image(png(), 'group-session', 'msg1', 'a', 100)
    async def scenario():
        p.adapter = AsyncMock(return_value={'status': 'ok', 'retcode': 0, 'data': None})
        avatar = await p.propose('avatar', {'image_id': image['id']})
        assert (await p.execute(avatar['id']))['state'] == 'confirmed'
        p.adapter.assert_awaited_once()
        p.adapter = AsyncMock(side_effect=[{'user_id': 123456, 'nickname': 'old'}, {'status': 'ok', 'retcode': 0, 'data': None}, {'user_id': 123456, 'nickname': 'new'}])
        name = await p.propose('nickname', {'nickname': 'new'})
        assert (await p.execute(name['id']))['state'] == 'confirmed'
        assert p.store.get('previous_nickname') == 'old'
    run(scenario())
    p.store.close()


def test_live_album_response_shape():
    async def scenario():
        q = await authenticated_qzone(lambda r: httpx.Response(200, text='shine0_Callback({"code":0,"data":{"albumListModeSort":[],"albumsInUser":0}});'))
        assert await q.albums() == []
    run(scenario())


def test_cancellation_marks_unknown_and_never_resends(tmp_path):
    p = plugin(tmp_path)
    p.verify_account = AsyncMock()
    entered = asyncio.Event()
    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()
    p.qzone = lambda: SimpleNamespace(authenticate=AsyncMock(), publish=blocked)
    async def scenario():
        job = await p.propose('post', {'text': 'waiting'})
        task = asyncio.create_task(p.execute(job['id']))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert p.store.job(job['id'])['state'] == 'unknown'
    run(scenario())
    p.store.close()
