import base64
from io import BytesIO
import warnings

from PIL import Image, ImageOps


def normalize_image(encoded: str, max_mb: int) -> bytes:
    if not isinstance(encoded, str) or len(encoded) > (max_mb * 1024 * 1024 * 4 // 3 + 8):
        raise ValueError('图片超过大小限制')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError('图片编码无效') from None
    if len(raw) > max_mb * 1024 * 1024:
        raise ValueError('图片超过大小限制')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > 25000000:
                raise ValueError('图片分辨率过大')
            source.seek(0)
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((2048, 2048))
            output = BytesIO()
            image.save(output, format='PNG')
    result = output.getvalue()
    if len(result) > max_mb * 1024 * 1024:
        raise ValueError('标准化图片仍超过大小限制')
    return result


async def fetch_image(ctx, chat_id, message_id, index=0):
    item = await ctx.message.get_by_id(message_id, chat_id=chat_id, include_binary_data=True)
    if not isinstance(item, dict):
        raise ValueError('找不到当前会话里的图片消息')
    raw = item.get('raw_message') or []
    images = [seg for seg in raw if isinstance(seg, dict) and seg.get('type') in ('image', 'emoji')]
    if not images:
        target = str(item.get('reply_to') or '')
        for seg in raw:
            if seg.get('type') == 'reply' and isinstance(seg.get('data'), dict):
                target = str(seg['data'].get('target_message_id') or target)
        if target:
            item = await ctx.message.get_by_id(target, chat_id=chat_id, include_binary_data=True)
            if not isinstance(item, dict):
                raise ValueError('引用的图片不在当前会话')
            images = [seg for seg in item.get('raw_message', []) if seg.get('type') in ('image', 'emoji')]
            message_id = target
    if index < 0 or index >= len(images):
        raise ValueError('指定位置没有图片')
    value = images[index].get('binary_data_base64')
    if not value:
        raise ValueError('图片数据尚未就绪，不会保存空文件')
    return value, message_id
