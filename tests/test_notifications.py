import asyncio
from unittest.mock import AsyncMock

import pytest

from qq_life_lite.qzone import RemoteFailure, UncertainResult
from qq_life_lite.store import Store
from test_life import plugin, run


def setup_notifications(tmp_path):
    p = plugin(tmp_path)
    p.verify_account = AsyncMock()
    p.ctx.chat.open_session = AsyncMock(return_value={
        'session_id': 'owner-private', 'platform': 'qq', 'user_id': '999999',
        'account_id': '123456', 'group_id': None, 'is_group_session': False, 'chat_type': 'private',
    })
    p.ctx.send.text.return_value = {'sent': True, 'message_id': 'notification1'}
    client = type('Client', (), {'authenticate': AsyncMock(), 'publish': AsyncMock(return_value={'tid': 'post1'})})()
    p.qzone = lambda: client
    return p, client


def test_confirmed_post_notifies_owner_once_with_space_link(tmp_path):
    p, client = setup_notifications(tmp_path)
    async def scenario():
        draft = await p.propose('post', {'text': 'a new post'})
        p.ctx.send.text.assert_not_awaited()
        result = await p.execute(draft['id'])
        assert result['state'] == 'confirmed'
        text, sid = p.ctx.send.text.call_args.args
        assert sid == 'owner-private'
        assert '我刚发了一条说说' in text and 'https://user.qzone.qq.com/123456' in text
        assert p.ctx.send.text.call_args.kwargs['return_details'] is True
        p.ctx.chat.open_session.assert_awaited_once_with(platform='qq', chat_type='private', user_id='999999', account_id='123456')
        await p.notify_post_owners()
        assert p.ctx.send.text.await_count == 1
        assert p.store.notification_status()[0]['state'] == 'sent'
        assert client.publish.await_count == 1
    run(scenario())
    p.store.close()


@pytest.mark.parametrize('error,state', [(RemoteFailure('rejected'), 'failed'), (UncertainResult('timeout'), 'unknown')])
def test_failed_or_uncertain_post_never_announces_success(tmp_path, error, state):
    p, client = setup_notifications(tmp_path)
    client.publish.side_effect = error
    async def scenario():
        draft = await p.propose('post', {'text': 'test'})
        result = await p.execute(draft['id'])
        assert result['state'] == state
        assert p.store.notification_status() == []
        p.ctx.send.text.assert_not_awaited()
    run(scenario())
    p.store.close()


def test_notification_failure_keeps_post_confirmed(tmp_path):
    p, client = setup_notifications(tmp_path)
    p.ctx.send.text.side_effect = RuntimeError('offline')
    async def scenario():
        draft = await p.propose('post', {'text': 'test'})
        assert (await p.execute(draft['id']))['state'] == 'confirmed'
        await p.notify_post_owners()
        assert p.store.notification_status()[0]['state'] == 'unknown'
        assert client.publish.await_count == 1 and p.ctx.send.text.await_count == 1
    run(scenario())
    p.store.close()


def test_pending_notification_survives_restart_without_republishing(tmp_path):
    p, client = setup_notifications(tmp_path)
    draft = p.store.queue('post', {'text': 'test', 'image_ids': []})
    p.store.finish(draft['id'], 'confirmed', {'tid': 'post1'}, notify_owners=['999999', '999999'], bot_uin='123456')
    p.store.close()
    p.store = Store(tmp_path)
    run(p.notify_post_owners())
    assert p.ctx.send.text.await_count == 1
    p.store.close()
    p.store = Store(tmp_path)
    run(p.notify_post_owners())
    assert p.ctx.send.text.await_count == 1
    client.publish.assert_not_awaited()
    p.store.close()


def test_interrupted_notification_stays_unknown_on_restart(tmp_path):
    p, _ = setup_notifications(tmp_path)
    draft = p.store.queue('post', {'text': 'test', 'image_ids': []})
    p.store.finish(draft['id'], 'confirmed', {'tid': 'post1'}, notify_owners=['999999'], bot_uin='123456')
    assert p.store.claim_notification(draft['id'], '999999')
    p.store.close()
    p.store = Store(tmp_path)
    run(p.notify_post_owners())
    assert p.store.notification_status()[0]['state'] == 'unknown'
    p.ctx.send.text.assert_not_awaited()
    assert p.store.job(draft['id'])['state'] == 'confirmed'
    p.store.close()


def test_wrong_account_or_group_route_is_rejected(tmp_path):
    p, _ = setup_notifications(tmp_path)
    p.ctx.chat.open_session.return_value['group_id'] = '111111'
    draft = p.store.queue('post', {'text': 'test', 'image_ids': []})
    p.store.finish(draft['id'], 'confirmed', {'tid': 'post1'}, notify_owners=['999999'], bot_uin='123456')
    run(p.notify_post_owners())
    assert p.store.notification_status()[0]['state'] == 'failed'
    p.ctx.send.text.assert_not_awaited()
    p.store.close()


def test_notification_cancel_does_not_revert_published_post(tmp_path):
    p, _ = setup_notifications(tmp_path)
    started = asyncio.Event()
    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    p.ctx.send.text.side_effect = blocked
    async def scenario():
        draft = await p.propose('post', {'text': 'test'})
        task = asyncio.create_task(p.execute(draft['id']))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert p.store.job(draft['id'])['state'] == 'confirmed'
        assert p.store.notification_status()[0]['state'] == 'unknown'
    run(scenario())
    p.store.close()


def test_non_post_operations_do_not_notify(tmp_path):
    p, _ = setup_notifications(tmp_path)
    draft = p.store.queue('nickname', {'nickname': 'new'})
    p.store.finish(draft['id'], 'confirmed', {'nickname': 'new'}, notify_owners=['999999'], bot_uin='123456')
    run(p.notify_post_owners())
    p.ctx.send.text.assert_not_awaited()
    p.store.close()
