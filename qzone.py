import base64
from http.cookies import SimpleCookie
import json
from urllib.parse import parse_qs, urlsplit

import httpx


class RemoteFailure(RuntimeError):
    pass


class UncertainResult(RuntimeError):
    pass


def decode_response(text):
    start = text.find('{')
    if start < 0:
        raise ValueError('缺少 JSON 响应')
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError('响应不是对象')
    return value


def unwrap(value):
    if not isinstance(value, dict):
        raise RemoteFailure('适配器未返回有效回执')
    if value.get('success') is False or value.get('status') == 'failed' or value.get('retcode', 0) != 0:
        raise RemoteFailure('适配器拒绝请求，请检查客户端状态')
    return value.get('data', value)


class Qzone:
    def __init__(self, call, uin, transport=None):
        self.call = call
        self.uin = uin
        self.transport = transport
        self.cookies = {}
        self.gtk = ''

    async def authenticate(self):
        login = unwrap(await self.call('system.get_login_info'))
        if not isinstance(login, dict) or str(login.get('user_id')) != self.uin:
            raise RemoteFailure('当前 QQ 登录账号与插件配置不一致')
        value = unwrap(await self.call('account.get_cookies', params={'domain': 'qzone.qq.com'}))
        raw = value.get('cookies', '') if isinstance(value, dict) else ''
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            raise RemoteFailure('空间登录状态无法解析') from None
        self.cookies = {key: morsel.value for key, morsel in jar.items()}
        secret = self.cookies.get('p_skey')
        if not secret:
            raise RemoteFailure('NapCat 未提供有效空间 p_skey；需要重新建立空间登录，当前不会提交操作')
        value = 5381
        for char in secret:
            value = ((value << 5) + value + ord(char)) & 0x7fffffff
        self.gtk = str(value)
        return {'account_matches': True, 'cookie_available': True}

    async def request(self, method, path, data=None, query=None):
        if not self.gtk:
            await self.authenticate()
        url = 'https://user.qzone.qq.com/proxy/domain/' + path
        if path == 'upload':
            url = 'https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image'
        headers = {'Referer': f'https://user.qzone.qq.com/{self.uin}', 'Origin': 'https://user.qzone.qq.com', 'User-Agent': 'Mozilla/5.0'}
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, transport=self.transport, cookies=self.cookies) as client:
                response = await client.request(method, url, params={'g_tk': self.gtk, **(query or {})}, data=data, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise RemoteFailure('未连接到空间服务器，本次未确认提交') from None
        except httpx.HTTPError:
            if method == 'POST':
                raise UncertainResult('请求已发出但回执未知，禁止自动重试') from None
            raise RemoteFailure('读取空间失败') from None
        if response.status_code != 200:
            error = UncertainResult if method == 'POST' else RemoteFailure
            raise error(f'空间 HTTP {response.status_code}，操作结果未确认')
        try:
            result = decode_response(response.text)
        except ValueError:
            error = UncertainResult if method == 'POST' else RemoteFailure
            raise error('空间回执不可解析，结果未确认') from None
        code = result.get('code', result.get('ret'))
        if code is None:
            error = UncertainResult if method == 'POST' else RemoteFailure
            raise error('空间回执缺少状态码')
        if code not in (0, '0'):
            raise RemoteFailure(f'空间拒绝操作，业务代码={code}；请检查登录或账户限制')
        return result

    async def albums(self):
        result = await self.request('GET', 'photo.qzone.qq.com/fcgi-bin/fcg_list_album_v3', query={
            'uin': self.uin, 'hostUin': self.uin, 'inCharset': 'utf-8', 'outCharset': 'utf-8',
            'format': 'jsonp', 'appid': 4, 'source': 'qzone', 'plat': 'qzone', 'notice': 0,
            'filter': 1, 'handset': 4, 'pageNumModeSort': 200, 'pageNumModeClass': 200,
            'needUserInfo': 1, 'idcNum': 4, 'callbackFun': 'shine0', 'callback': 'shine0_Callback',
        })
        data = result.get('data')
        if not isinstance(data, dict):
            raise RemoteFailure('相册列表缺少 data，不能认定为空')
        albums = data.get('albumListModeSort', data.get('albumList', data.get('albumlist')))
        if albums is None and isinstance(data.get('albumListModeClass'), list):
            albums = [item for group in data['albumListModeClass'] for item in group.get('albumList', [])]
        if albums is None and data.get('albumsInUser') == 0:
            albums = []
        if not isinstance(albums, list):
            raise RemoteFailure('相册列表格式不匹配')
        return [{'id': str(a.get('id') or a.get('albumid') or ''), 'name': str(a.get('name') or a.get('albumname') or '')} for a in albums if isinstance(a, dict)]

    async def feeds(self):
        result = await self.request('GET', 'taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6', query={'uin': self.uin, 'ftype': 0, 'sort': 0, 'pos': 0, 'num': 3, 'format': 'json', 'code_version': 1})
        return [{'id': str(v.get('tid', '')), 'time': v.get('created_time')} for v in result.get('msglist') or []]

    async def upload(self, blob, album_id=''):
        if not self.gtk:
            await self.authenticate()
        fields = {'uin': self.uin, 'p_uin': self.uin, 'zzpaneluin': self.uin,
                  'skey': self.cookies.get('skey', ''), 'p_skey': self.cookies.get('p_skey', ''),
                  'filename': 'collection.png', 'uploadtype': '1', 'albumtype': '0' if album_id else '7',
                  'exttype': '0', 'refer': 'album' if album_id else 'shuoshuo', 'output_type': 'json',
                  'charset': 'utf-8', 'output_charset': 'utf-8', 'upload_hd': '1', 'hd_width': '2048',
                  'hd_height': '10000', 'hd_quality': '96', 'base64': '1', 'picfile': base64.b64encode(blob).decode()}
        if album_id:
            fields['albumid'] = album_id
        result = await self.request('POST', 'upload', fields)
        data = result.get('data') or {}
        if not isinstance(data, dict) or not data.get('lloc') or not data.get('albumid'):
            raise UncertainResult('上传回执缺少照片或相册 ID，需要核对')
        if album_id and str(data['albumid']) != album_id:
            raise UncertainResult('服务端返回的相册 ID 与指定相册不一致，需要核对')
        return data

    async def publish(self, text, images):
        fields = {'con': text, 'hostuin': self.uin, 'format': 'json', 'who': '1', 'ugc_right': '1',
                  'syn_tweet_verson': '1', 'paramstr': '1', 'feedversion': '1', 'ver': '1', 'code_version': '1',
                  'to_sign': '0', 'qzreferrer': f'https://user.qzone.qq.com/{self.uin}'}
        picture_tokens, pictures = [], []
        for blob in images:
            data = await self.upload(blob)
            uri = data.get('url') or data.get('pre') or ''
            token = parse_qs(urlsplit(uri).query).get('bo', [''])[0]
            required = ['albumid', 'lloc', 'sloc', 'type', 'height', 'width']
            if not token or any(key not in data for key in required):
                raise RemoteFailure('图片已上传，但附图回执不完整，未提交说说')
            picture_tokens.append(token)
            values = ['', data['albumid'], data['lloc'], data['sloc'], data['type'], data['height'], data['width'], '', data['height'], data['width']]
            pictures.append(','.join(map(str, values)))
        if pictures:
            fields.update(pic_bo=','.join(picture_tokens), richval='\t'.join(pictures), richtype='1')
        result = await self.request('POST', 'taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6', fields)
        if not result.get('tid'):
            raise UncertainResult('空间返回成功但没有动态 ID，需要核对')
        return {'tid': str(result['tid'])}
