import assert from 'node:assert/strict';
import { beforeEach, mock, test } from 'node:test';

const context = { postId: 'post-1' };
const redis = { get: mock.fn(), incrBy: mock.fn() };
const reddit = { getCurrentUsername: mock.fn() };
mock.module('@devvit/web/server', { namedExports: { context, redis, reddit } });
const { api } = await import('./api.ts');
const { forms } = await import('./forms.ts');

beforeEach(() => {
  context.postId = 'post-1';
  redis.get.mock.resetCalls();
  redis.incrBy.mock.resetCalls();
  redis.get.mock.mockImplementation(async () => '7');
  redis.incrBy.mock.mockImplementation(async (_key, delta) => 7 + delta);
  reddit.getCurrentUsername.mock.mockImplementation(async () => 'editor');
});

test('initialization returns persisted count and current identity', async () => {
  const response = await api.request('/init');
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { type: 'init', postId: 'post-1', count: 7, username: 'editor' });
});

test('new anonymous sessions start at zero', async () => {
  redis.get.mock.mockImplementation(async () => undefined);
  reddit.getCurrentUsername.mock.mockImplementation(async () => undefined);
  const response = await api.request('/init');
  assert.deepEqual(await response.json(), { type: 'init', postId: 'post-1', count: 0, username: 'anonymous' });
});

test('missing post context rejects reads and writes without touching storage', async () => {
  context.postId = undefined;
  for (const [path, method] of [['/init', 'GET'], ['/increment', 'POST'], ['/decrement', 'POST']]) {
    const response = await api.request(path, { method });
    assert.equal(response.status, 400);
    assert.equal((await response.json()).status, 'error');
  }
  assert.equal(redis.get.mock.callCount(), 0);
  assert.equal(redis.incrBy.mock.callCount(), 0);
});

test('initialization reports storage failure', async () => {
  redis.get.mock.mockImplementation(async () => { throw new Error('unavailable'); });
  const response = await api.request('/init');
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { status: 'error', message: 'Initialization failed: unavailable' });
});

for (const [action, delta] of [['increment', 1], ['decrement', -1]]) {
  test(`${action} uses an atomic storage update`, async () => {
    const response = await api.request(`/${action}`, { method: 'POST' });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { type: action, postId: 'post-1', count: 7 + delta });
    assert.deepEqual(redis.incrBy.mock.calls[0].arguments, ['count', delta]);
  });
}

test('form submission trims text and handles missing or non-string messages', async () => {
  for (const [body, expected] of [[{ message: ' hello ' }, 'Form says: hello'], [{}, 'Form submitted with no message'], [{ message: 7 }, 'Form submitted with no message']]) {
    const response = await forms.request('/example-submit', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { showToast: expected });
  }
});
