"""Local HTTP fixtures exercise hard deadlines; no device side effects."""
from contextlib import contextmanager
import http.server
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scheduler'))
import server_client as client


class Budget:
    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds

    def remaining(self):
        return max(0, self.deadline - time.monotonic())


@contextmanager
def server(callback):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                callback(self)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # HTTPServer asks reverse DNS for server_name; fixtures must stay offline.
    with patch.object(socket, 'getfqdn', return_value='localhost'):
        httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}'
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(1)


def respond(handler, value, code=200):
    body = json.dumps(value).encode()
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.token = Path(self.folder.name) / 'token'
        self.token.write_text('secret-device-token\n')

    def make_client(self, url, **overrides):
        return client.ServerClient(dict(base_url=url, token_file=str(self.token),
                                         timeout_sec=.8, total_timeout_sec=2,
                                         backoff_sec=0, **overrides), Budget(10))

    def test_success_auth_identity_and_latency(self):
        observed = []
        def reply(handler):
            observed.append((handler.path, handler.headers['Authorization'], handler.body))
            respond(handler, {'debug': True, 'display': 'intentionally invalid'})
        with server(reply) as url:
            api = self.make_client(url)
            value = api.check_in({'event_id': 'cycle-1'})
        self.assertTrue(value['debug'])
        self.assertGreater(api.last_latency_ms, 0)
        self.assertEqual(observed, [('/api/v1/check-in', 'Bearer secret-device-token',
                                    {'event_id': 'cycle-1', 'device_id': 'epaper-pi-zero2'})])
        with self.assertRaises(ValueError):
            client.validate_display(value['display'])

    def test_transient_status_retried_but_auth_and_bad_request_not_retried(self):
        for status, attempts in ((503, 3), (401, 1), (403, 1), (400, 1), (302, 1)):
            observed = []
            def reply(handler):
                observed.append(handler.path)
                respond(handler, {'error': 'private server details'}, status)
            with self.subTest(status=status), server(reply) as url:
                api = self.make_client(url)
                with self.assertRaises(client.ServerError) as error:
                    api.check_in({})
                self.assertEqual(error.exception.status_code, status)
                self.assertNotIn('private', str(error.exception))
                self.assertEqual(len(observed), attempts)

    def test_network_refused_retries_and_has_finite_deadline(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        api = self.make_client(f'http://127.0.0.1:{port}')
        start = time.monotonic()
        with self.assertRaises(client.ServerError):
            api.check_in({})
        self.assertLess(time.monotonic() - start, 2.2)

    def test_fragmented_headers_and_body_obey_absolute_deadline(self):
        for headers in (True, False):
            def reply(handler):
                if headers:
                    fragments = b'HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n{"debug":true}'
                else:
                    handler.send_response(200)
                    handler.send_header('Content-Length', '14')
                    handler.end_headers()
                    fragments = b'{"debug":true}'
                for byte in fragments:
                    handler.wfile.write(bytes([byte]))
                    handler.wfile.flush()
                    time.sleep(.12)
            with self.subTest(headers=headers), server(reply) as url:
                api = self.make_client(url)
                api.cfg.update(timeout_sec=.35, total_timeout_sec=.6)
                start = time.monotonic()
                with self.assertRaises(client.ServerError):
                    api.check_in({})
                self.assertLess(time.monotonic() - start, .9)

    def test_global_budget_shortens_request(self):
        def reply(handler):
            time.sleep(2)
            respond(handler, {'debug': False})
        with server(reply) as url:
            api = self.make_client(url)
            api.budget = Budget(.25)
            start = time.monotonic()
            with self.assertRaises(client.ServerError):
                api.check_in({})
            self.assertLess(time.monotonic() - start, .55)

    def test_invalid_json_debug_and_oversized_response_are_not_retried(self):
        for kind in ('json', 'debug', 'oversized', 'oversized-unframed'):
            observed = []
            def reply(handler):
                observed.append(True)
                if kind == 'debug':
                    respond(handler, {'debug': 1})
                    return
                handler.send_response(200)
                if kind == 'oversized':
                    handler.send_header('Content-Length', str(client.MAX_RESPONSE_BYTES + 1))
                handler.end_headers()
                handler.wfile.write(b'x' * (client.MAX_RESPONSE_BYTES + 1) if kind.startswith('oversized') else b'{')
            with self.subTest(kind=kind), server(reply) as url:
                with self.assertRaises(client.ServerError):
                    self.make_client(url).check_in({})
                self.assertEqual(len(observed), 1)

    def test_report_requires_ack_and_payload_is_bounded(self):
        with server(lambda h: respond(h, {'accepted': False})) as url:
            api = self.make_client(url)
            with self.assertRaises(client.ServerError):
                api.report({'event_id': 'one'})
            with self.assertRaises(ValueError):
                api.report({'blob': 'x' * client.MAX_PAYLOAD_BYTES})
            with self.assertRaises(ValueError):
                api.report({'device_id': 'other'})
            with self.assertRaises(ValueError):
                api.report({'bad': float('nan')})

    def test_incomplete_body_is_retried_even_if_prefix_is_valid_json(self):
        observed = []
        def reply(handler):
            observed.append(True)
            handler.send_response(200)
            handler.send_header('Content-Length', '100')
            handler.end_headers()
            handler.wfile.write(b'{"debug":true}')
        with server(reply) as url:
            with self.assertRaises(client.ServerError):
                self.make_client(url).check_in({})
        self.assertEqual(len(observed), 3)

    def test_invalid_configuration_prevents_dns_and_credentials_in_url(self):
        for url in ('http://example.com:8088', 'ftp://127.0.0.1',
                    'http://user:secret@127.0.0.1', 'http://127.0.0.1/path',
                    'http://127.0.0.1/?secret=token', 'http://127.0.0.1:99999'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.make_client(url)
        client.validate_server_config({'base_url': 'http://[::1]:8088'})
        for overrides in ({'max_attempts': 4}, {'timeout_sec': 6},
                          {'total_timeout_sec': 21}, {'backoff_sec': -1},
                          {'timeout_sec': float('nan')}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                client.validate_server_config(overrides)


class DisplayTests(unittest.TestCase):
    def test_fixed_ascii_limits_and_types(self):
        good = {'title': 't' * 40, 'status': 's' * 24,
                'rows': [{'label': 'l' * 20, 'value': 'v' * 48} for _ in range(6)]}
        self.assertEqual(client.validate_display(good), good)
        self.assertEqual(client.validate_display(dict(good, rows=[{'label': 'x', 'value': ''}]))['rows'][0]['value'], '')
        invalid = [dict(good, title=''), dict(good, title='t' * 41), dict(good, status='s' * 25),
                   dict(good, status=''), dict(good, title='中文'), dict(good, title='line\n'),
                   dict(good, rows=good['rows'] + good['rows']), dict(good, rows={}),
                   dict(good, rows=[{'label': '', 'value': ''}]),
                   dict(good, rows=[{'label': 'l' * 21, 'value': ''}]),
                   dict(good, rows=[{'label': 'x', 'value': 'v' * 49}]),
                   dict(good, rows=[{'label': 'x', 'value': None}])]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.validate_display(value)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'outbox.json'

    def test_overflow_restart_and_duplicate_identity(self):
        queue = client.ReportOutbox(self.path, limit=2)
        for event in ('a', 'b', 'c'):
            queue.enqueue({'device_id': 'one', 'event_id': event})
        self.assertEqual(queue.dropped_count, 1)
        self.assertEqual(queue.pending_count, 2)
        queue = client.ReportOutbox(self.path, limit=2)
        queue.enqueue({'device_id': 'one', 'event_id': 'c', 'new': True})
        self.assertEqual(queue.pending_count, 2)
        self.assertEqual([r['event_id'] for r in queue.reports], ['b', 'c'])
        self.assertTrue(queue.reports[-1]['new'])
        self.assertEqual(queue.dropped_count, 1)

    def test_failed_ack_retained_and_success_removed_in_order(self):
        queue = client.ReportOutbox(self.path)
        for event in ('a', 'b', 'c'):
            queue.enqueue({'event_id': event})
        api = Mock(cfg={'total_timeout_sec': 20})
        api.report.side_effect = [{'accepted': True}, client.ServerError('offline')]
        result = queue.flush(api, Budget(10))
        self.assertEqual(result, {'sent': 1, 'pending': 2, 'dropped': 0, 'error': 'offline'})
        self.assertEqual([r['event_id'] for r in client.ReportOutbox(self.path).reports], ['b', 'c'])
        api.report.reset_mock(side_effect=True)
        result = queue.flush(api, Budget(10))
        self.assertEqual(result['sent'], 2)
        self.assertEqual(client.ReportOutbox(self.path).pending_count, 0)

    def test_flush_uses_one_shared_budget(self):
        queue = client.ReportOutbox(self.path)
        for event in range(5):
            queue.enqueue({'event_id': str(event)})
        api = Mock(cfg={'total_timeout_sec': .25})
        budgets = []
        def report(payload, budget):
            budgets.append(budget.remaining())
            time.sleep(min(.15, budget.remaining()))
            if budget.remaining() <= 0:
                raise client.ServerError('deadline')
        api.report.side_effect = report
        start = time.monotonic()
        result = queue.flush(api, Budget(10))
        self.assertLess(time.monotonic() - start, .4)
        self.assertEqual(result['sent'], 1)
        self.assertLess(budgets[1], budgets[0])

    def test_corrupt_queue_is_preserved_and_invalid_payload_not_written(self):
        self.path.write_text('broken data')
        with self.assertRaisesRegex(ValueError, 'preserved'):
            client.ReportOutbox(self.path)
        self.assertEqual(self.path.read_text(), 'broken data')
        self.path.unlink()
        queue = client.ReportOutbox(self.path)
        with self.assertRaises(ValueError):
            queue.enqueue({'blob': 'x' * client.MAX_PAYLOAD_BYTES})
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
