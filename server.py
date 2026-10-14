"""Read-only single-account IMAP bridge. Secrets are server environment only."""
import base64
import hashlib
import hmac
import imaplib
import json
import os
import re
import ssl
import socket
import sys
import traceback
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

MAX_MAIL = 8 * 1024 * 1024
HOST = 'imap.spmode.ne.jp'

class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in ('br', 'p', 'div'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

def parse_mail(raw, uid, validity, internaldate=''):
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    text, html, attachments = [], [], []
    for index, part in enumerate(msg.walk()):
        if part.is_multipart(): continue
        filename = part.get_filename()
        if filename or part.get_content_disposition() == 'attachment':
            attachments.append({'name': filename or 'attachment', 'part': index,
                                'pdf': part.get_content_type() == 'application/pdf' or (filename or '').lower().endswith('.pdf')})
        elif part.get_content_type() in ('text/plain', 'text/html'):
            try: value = part.get_content()
            except (LookupError, UnicodeError):
                value = (part.get_payload(decode=True) or b'').decode('utf-8', errors='replace')
            if part.get_content_type() == 'text/plain': text.append(value)
            else:
                p = TextHTML(); p.feed(value); html.append(''.join(p.parts))
    # IMAP dates filter by INTERNALDATE, so display that same date in Japan time.
    received = ''
    from datetime import timezone
    for value in (internaldate, str(msg.get('Date', ''))):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
            received = parsed.astimezone(timezone(timedelta(hours=9))).date().isoformat(); break
        except (ValueError, TypeError, OverflowError): pass
    account = hashlib.sha256(os.environ.get('DOCOMO_USER', '').encode()).hexdigest()[:16]
    return {'id': f'imap-{account}-{validity}-{uid}', 'type': 'imap', 'name': 'ドコモ受信箱',
            'uid': uid, 'validity': validity, 'from': str(msg.get('From', '')),
            'subject': str(msg.get('Subject', '')) or '（件名なし）', 'date': received,
            'body': '\n'.join(text or html)[:200000], 'pdf': '', 'attachments': attachments}

class PublicError(Exception): pass

def connection():
    try:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        client = imaplib.IMAP4_SSL(HOST, 993, ssl_context=context, timeout=25)
    except socket.gaierror:
        raise PublicError('接続先の名前解決に失敗しました（DNS）。Renderからドコモへの通信を確認する必要があります。') from None
    except (TimeoutError, socket.timeout):
        raise PublicError('ドコモ受信サーバーへの接続が時間切れです（IMAP接続）。パスワード認証前に失敗しています。') from None
    except ssl.SSLError:
        raise PublicError('ドコモ受信サーバーとの暗号化接続に失敗しました（TLS）。') from None
    except OSError:
        raise PublicError('Renderからドコモ受信サーバーに接続できません（TCP接続）。') from None
    try:
        user = os.environ['DOCOMO_USER'].strip()
        password = os.environ['DOCOMO_PASSWORD'].strip()
        if not user or not password:
            raise PublicError('DOCOMO_USER・DOCOMO_PASSWORDが空欄です。Renderの設定を確認してください。')
        try:
            user.encode('ascii'); password.encode('ascii')
        except UnicodeEncodeError:
            raise PublicError('接続設定に全角文字が含まれています。ユーザーIDとIMAP専用パスワードを半角で入力してください。') from None
        try:
            client.login(user, password)
        except imaplib.IMAP4.error:
            raise PublicError('ドコモの認証に失敗しました（IMAP認証）。ユーザーID・IMAP専用パスワード・dアカウント利用設定を確認してください。') from None
        except TimeoutError:
            raise PublicError('ドコモの認証応答が時間切れです（IMAP認証）。') from None
        if client.select('INBOX', readonly=True)[0] != 'OK': raise PublicError('受信箱を開けませんでした。')
        value = client.response('UIDVALIDITY')[1]
        validity = int(value[0]) if value and value[0] else 0
        if not validity: raise PublicError('受信箱の識別情報を取得できませんでした。')
        return client, validity
    except Exception:
        try: client.logout()
        except Exception: pass
        raise

def fetch_raw(client, uid):
    status, data = client.uid('fetch', str(uid), '(RFC822.SIZE)')
    match = re.search(rb'RFC822.SIZE (\d+)', b' '.join(x for x in (data or []) if isinstance(x, bytes)))
    if status != 'OK' or not match: raise PublicError('メールが削除または移動されています。')
    if int(match[1]) > MAX_MAIL: raise PublicError('8MBを超えるため、このメールは取込対象外です。')
    status, data = client.uid('fetch', str(uid), '(BODY.PEEK[] INTERNALDATE)')
    if status != 'OK': raise PublicError('メールを取得できませんでした。')
    for item in data or []:
        if isinstance(item, tuple):
            match = re.search(rb'INTERNALDATE "([^"]+)"', item[0])
            return item[1], match[1].decode('ascii') if match else ''
    raise PublicError('メールが削除または移動されています。')

def positive(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 4294967295:
        raise PublicError('取込位置が不正です。最初から取得してください。')
    return value

def sync(params):
    criteria = ['ALL']
    for key, command in (('start', 'SINCE'), ('end', 'BEFORE')):
        if params.get(key):
            try:
                d = date.fromisoformat(params[key])
                if key == 'end': d += timedelta(days=1)
            except (ValueError, TypeError, OverflowError): raise PublicError('日付を確認してください。')
            criteria += [command, d.strftime('%d-') + ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.month-1] + d.strftime('-%Y')]
    if params.get('start') and params.get('end') and params['start'] > params['end']:
        raise PublicError('開始日が終了日より後です。')
    before = positive(params['before']) if params.get('before') is not None else None
    client, validity = connection()
    try:
        if before is not None:
            if params.get('validity') != validity: raise PublicError('受信箱が変更されています。最初から取得してください。')
            if before == 1: return {'items': [], 'next': None, 'warnings': []}
            criteria += ['UID', f'1:{before-1}']
        status, data = client.uid('search', None, *criteria)
        if status != 'OK': raise PublicError('受信箱の検索に失敗しました。')
        uids = sorted([int(x) for x in data[0].split()], reverse=True)
        selected = uids[:10]; items = []; warnings = []
        for uid in selected:
            try:
                raw, received = fetch_raw(client, uid)
                items.append(parse_mail(raw, uid, validity, received))
            except PublicError as e: warnings.append(f'UID {uid}: {e}')
        return {'items': items, 'next': {'before': selected[-1], 'validity': validity} if len(uids) > 10 else None,
                'warnings': warnings}
    finally:
        try: client.logout()
        except Exception: pass

def attachment(params):
    uid = positive(params.get('uid')); part_index = params.get('part')
    if isinstance(part_index, bool) or not isinstance(part_index, int) or part_index < 0:
        raise PublicError('添付ファイルの指定が不正です。')
    client, validity = connection()
    try:
        if params.get('validity') != validity: raise PublicError('受信箱が変更されています。再取得してください。')
        raw, _ = fetch_raw(client, uid)
        parts = list(BytesParser(policy=policy.default).parsebytes(raw).walk())
        if part_index >= len(parts): raise PublicError('添付ファイルがありません。')
        part = parts[part_index]
        if not part.get_filename() and part.get_content_disposition() != 'attachment':
            raise PublicError('添付ファイルがありません。')
        payload = part.get_payload(decode=True)
        if payload is None: raise PublicError('添付ファイルを読み取れません。')
        return {'name': part.get_filename() or 'attachment', 'data': base64.b64encode(payload).decode('ascii')}
    finally:
        try: client.logout()
        except Exception: pass

def app(environ, start_response):
    origin = os.environ.get('ALLOWED_ORIGIN', 'https://mdaidai0503.github.io')
    request_origin = environ.get('HTTP_ORIGIN', '')
    headers = [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store'),
               ('X-Content-Type-Options', 'nosniff'), ('Vary', 'Origin')]
    if request_origin == origin: headers += [('Access-Control-Allow-Origin', origin)]
    def reply(status, data):
        start_response(status, headers); return [json.dumps(data, ensure_ascii=False).encode()]
    if request_origin and request_origin != origin: return reply('403 Forbidden', {'error': 'このサイトからの接続は許可されていません。'})
    if environ.get('REQUEST_METHOD') == 'OPTIONS':
        headers.extend([('Access-Control-Allow-Methods', 'POST, GET, OPTIONS'), ('Access-Control-Allow-Headers', 'Authorization, Content-Type')])
        return reply('200 OK', {})
    if environ.get('PATH_INFO') == '/health' and environ.get('REQUEST_METHOD') == 'GET':
        return reply('200 OK', {'ok': True})
    token = os.environ.get('IMPORT_TOKEN', '')
    if len(token) < 24 or not os.environ.get('DOCOMO_USER') or not os.environ.get('DOCOMO_PASSWORD'):
        return reply('503 Service Unavailable', {'error': 'サーバーの接続設定が未完了です。'})
    supplied = environ.get('HTTP_AUTHORIZATION', '')
    if not hmac.compare_digest(supplied.encode(), ('Bearer '+token).encode()):
        return reply('401 Unauthorized', {'error': '取込キーを確認してください。'})
    if environ.get('REQUEST_METHOD') != 'POST': return reply('405 Method Not Allowed', {'error': 'POSTが必要です。'})
    try:
        size = int(environ.get('CONTENT_LENGTH') or 0)
        if size < 0 or size > 4096: raise PublicError('リクエストが大きすぎます。')
        params = json.loads(environ['wsgi.input'].read(size))
        if not isinstance(params, dict): raise PublicError('入力形式が不正です。')
        route = environ.get('PATH_INFO')
        if route == '/api/sync': result = sync(params)
        elif route == '/api/attachment': result = attachment(params)
        else: return reply('404 Not Found', {'error': '見つかりません。'})
        return reply('200 OK', result)
    except PublicError as e: return reply('400 Bad Request', {'error': str(e)})
    except (ValueError, TypeError, json.JSONDecodeError): return reply('400 Bad Request', {'error': '入力を確認してください。'})
    except imaplib.IMAP4.error: return reply('502 Bad Gateway', {'error': 'ドコモメールへの接続に失敗しました。dアカウントID・IMAP専用パスワード・dアカウント利用設定を確認してください。'})
    except TimeoutError:
        return reply('504 Gateway Timeout', {'error': 'ドコモ接続後のメール取得が時間切れです（IMAP取得）。'})
    except OSError:
        return reply('502 Bad Gateway', {'error': 'メール取得中に通信が切れました（IMAP通信）。'})
    except Exception as e:
        # Log only type and stack locations; exception text may contain private mail.
        frames = traceback.extract_tb(e.__traceback__)
        print('import_error', type(e).__name__, [(f.name, f.lineno) for f in frames], file=sys.stderr)
        return reply('502 Bad Gateway', {'error': 'メール解析の内部処理で失敗しました（'+type(e).__name__+'）。このエラー表示をお知らせください。'})
