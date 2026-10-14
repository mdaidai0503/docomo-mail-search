import io
import json
import os
import unittest
from unittest.mock import patch
import server

RAW = b'From: test@example.com\r\nSubject: =?UTF-8?B?5piG5biD?=\r\nDate: Wed, 14 Oct 2026 16:01:00 +0900\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n5YWl5bqr\r\n--x\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=FAX_81762144776.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERg==\r\n--x--\r\n'
ENV={'DOCOMO_USER':'test-id','DOCOMO_PASSWORD':'test-secret','IMPORT_TOKEN':'x'*32}
class FakeIMAP:
    def __init__(self, *a, **kw): self.calls=[]; self.readonly=False; self.closed=False
    def login(self,*a): pass
    def select(self, folder, readonly=False): self.readonly=readonly; return 'OK',[b'12']
    def response(self,*a): return 'UIDVALIDITY',[b'99']
    def logout(self): self.closed=True
    def uid(self,command,*args):
        self.calls.append((command,args))
        if command=='search': return 'OK',[b'1 2 3 4 5 6 7 8 9 10 11 12']
        if args[-1]=='(RFC822.SIZE)': return 'OK',[b'1 (RFC822.SIZE 600)']
        return 'OK',[(b'1 (INTERNALDATE "14-Oct-2026 16:01:00 +0900" BODY[] {600}',RAW),b')']

class Tests(unittest.TestCase):
    def request(self, path, body, auth='Bearer '+'x'*32, origin='https://mdaidai0503.github.io'):
        raw=json.dumps(body).encode(); status=[]
        result=server.app({'PATH_INFO':path,'REQUEST_METHOD':'POST','CONTENT_LENGTH':str(len(raw)),
          'wsgi.input':io.BytesIO(raw),'HTTP_AUTHORIZATION':auth,'HTTP_ORIGIN':origin},lambda s,h:status.append((s,dict(h))))
        return status[0],json.loads(b''.join(result))
    @patch.dict(os.environ,ENV)
    def test_decode_japanese_and_attachment(self):
        r=server.parse_mail(RAW,12,99)
        self.assertEqual(r['subject'],'昆布'); self.assertEqual(r['body'],'入庫')
        self.assertEqual(r['date'],'2026-10-14'); self.assertTrue(r['attachments'][0]['pdf'])
    @patch.dict(os.environ,ENV)
    def test_readonly_peek_and_pagination(self):
        fake=FakeIMAP()
        with patch.object(server.imaplib,'IMAP4_SSL',return_value=fake):
            r=server.sync({'start':'2026-01-01','end':'2026-10-14'})
        self.assertTrue(fake.readonly); self.assertTrue(fake.closed)
        self.assertEqual(r['items'][0]['uid'],12); self.assertEqual(r['next'],{'before':3,'validity':99})
        self.assertIn(('fetch',('12','(BODY.PEEK[] INTERNALDATE)')),fake.calls)
        self.assertIn('15-Oct-2026',fake.calls[0][1])
    @patch.dict(os.environ,ENV)
    def test_validity_changed(self):
        fake=FakeIMAP()
        with patch.object(server.imaplib,'IMAP4_SSL',return_value=fake):
            with self.assertRaises(server.PublicError): server.sync({'before':3,'validity':98})
        self.assertTrue(fake.closed)
    @patch.dict(os.environ,ENV)
    def test_api_auth_and_cors(self):
        s,r=self.request('/api/sync',{},auth='');self.assertTrue(s[0].startswith('401'))
        s,r=self.request('/api/sync',{},origin='https://evil.example');self.assertTrue(s[0].startswith('403'))
        self.assertNotIn('Access-Control-Allow-Origin',s[1]);self.assertEqual(s[1]['Cache-Control'],'no-store')
    @patch.dict(os.environ,ENV)
    def test_bad_date_and_uid(self):
        s,r=self.request('/api/sync',{'start':'bad'});self.assertTrue(s[0].startswith('400'))
        s,r=self.request('/api/attachment',{'uid':'1:*','part':1});self.assertTrue(s[0].startswith('400'))
    @patch.dict(os.environ,ENV)
    def test_attachment(self):
        with patch.object(server.imaplib,'IMAP4_SSL',return_value=FakeIMAP()):
            s,r=self.request('/api/attachment',{'uid':12,'validity':99,'part':2})
        self.assertTrue(s[0].startswith('200'));self.assertEqual(r['data'],'JVBERg==')
    @patch.dict(os.environ,ENV)
    def test_oversized_skips_without_body(self):
        fake=FakeIMAP(); original=fake.uid
        def uid(command,*args):
            if command=='fetch' and args[-1]=='(RFC822.SIZE)': return 'OK',[b'1 (RFC822.SIZE 99999999)']
            return original(command,*args)
        fake.uid=uid
        with patch.object(server.imaplib,'IMAP4_SSL',return_value=fake): r=server.sync({})
        self.assertEqual(len(r['warnings']),10);self.assertEqual(r['items'],[])
    @patch.dict(os.environ,ENV)
    def test_internaldate_uses_japan_day(self):
        r=server.parse_mail(RAW,12,99,'13-Oct-2026 23:30:00 +0000')
        self.assertEqual(r['date'],'2026-10-14')
    @patch.dict(os.environ,ENV)
    def test_connection_timeout_diagnosis(self):
        with patch.object(server.imaplib,'IMAP4_SSL',side_effect=TimeoutError):
            status, result = self.request('/api/sync', {})
        self.assertTrue(status[0].startswith('400'))
        self.assertIn('パスワード認証前', result['error'])
    @patch.dict(os.environ,ENV)
    def test_dns_diagnosis(self):
        with patch.object(server.imaplib,'IMAP4_SSL',side_effect=server.socket.gaierror):
            status, result = self.request('/api/sync', {})
        self.assertIn('DNS', result['error'])
    @patch.dict(os.environ,ENV)
    def test_auth_diagnosis_no_secret(self):
        fake=FakeIMAP()
        fake.login=lambda *args: (_ for _ in ()).throw(server.imaplib.IMAP4.error('private test-secret'))
        with patch.object(server.imaplib,'IMAP4_SSL',return_value=fake):
            status, result=self.request('/api/sync', {})
        self.assertIn('IMAP認証', result['error']); self.assertNotIn('test-secret',result['error'])
    @patch.dict(os.environ,ENV)
    def test_internal_error_diagnosis_no_secret(self):
        with patch.object(server,'sync',side_effect=RuntimeError('private mail content')):
            with patch.object(server.sys,'stderr',io.StringIO()) as log:
                status,result=self.request('/api/sync', {})
        self.assertIn('RuntimeError',result['error'])
        self.assertNotIn('private',result['error']+log.getvalue())
    def test_docomo_tls_keeps_certificate_verification(self):
        c=server.docomo_tls_context()
        self.assertEqual(c.verify_mode,server.ssl.CERT_REQUIRED)
        self.assertTrue(c.check_hostname)
        self.assertEqual(c.minimum_version,server.ssl.TLSVersion.TLSv1_2)
        self.assertEqual(c.maximum_version,server.ssl.TLSVersion.TLSv1_2)
        self.assertIn('AES128-SHA256',[x['name'] for x in c.get_ciphers()])
        self.assertGreaterEqual(c.security_level,2)
    @patch.dict(os.environ,ENV)
    def test_tls_reason(self):
        err=server.ssl.SSLError(1,'handshake');err.reason='SSLV3_ALERT_HANDSHAKE_FAILURE'
        with patch.object(server.imaplib,'IMAP4_SSL',side_effect=err):
            status,result=self.request('/api/sync',{})
        self.assertIn('SSLV3_ALERT_HANDSHAKE_FAILURE',result['error'])
if __name__=='__main__': unittest.main()
