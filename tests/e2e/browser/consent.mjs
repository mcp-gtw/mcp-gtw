import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { chromium } from 'playwright';

const issuer = process.env.TEST_ISSUER;
const callbackOrigin = 'https://127.0.0.1:19503';
const redirect = callbackOrigin + '/connector_platform_oauth_redirect';
const unrelated = callbackOrigin + '/unrelated';
const browser = await chromium.launch({
    headless: true,
    ...(process.env.TEST_CHROME ? { executablePath: process.env.TEST_CHROME } : {}),
});

try {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();
    const errors = [];
    let consentPosts = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
        if (message.type() === 'error') errors.push(message.text());
    });
    page.on('request', request => {
        if (request.url() === issuer + '/oauth/consent' && request.method() === 'POST') consentPosts++;
    });
    const registered = await context.request.post(issuer + '/oauth/register', {
        data: { redirect_uris: [redirect, unrelated], client_name: 'Cross-origin browser test' },
    });
    assert.equal(registered.status(), 201);
    const client = await registered.json();
    const authorize = async () => {
        const verifier = randomBytes(48).toString('base64url');
        const state = randomBytes(24).toString('base64url');
        const url = new URL(issuer + '/oauth/authorize');
        url.search = new URLSearchParams({
            response_type: 'code', client_id: client.client_id, redirect_uri: redirect,
            scope: 'mcp:access openid', resource: issuer + '/mcp', state,
            code_challenge: createHash('sha256').update(verifier).digest('base64url'),
            code_challenge_method: 'S256',
        }).toString();
        await page.goto(url.href);
        return { verifier, state };
    };
    const finish = async (action, state) => {
        const before = consentPosts;
        await page.getByRole('button', { name: action, exact: true }).click();
        await page.waitForURL(redirect + '?**');
        await page.getByRole('heading', { name: 'Client callback' }).waitFor();
        assert.equal(consentPosts, before + 1);
        const callback = new URL(page.url());
        assert.equal(callback.origin + callback.pathname, redirect);
        assert.equal(callback.searchParams.get('state'), state);
        assert.equal(callback.searchParams.get('iss'), issuer);
        return callback;
    };
    const first = await authorize();
    await page.locator('input[name=username]').fill('cross-origin-test');
    await page.locator('input[name=password]').fill('strong-local-password');
    await page.getByRole('button', { name: 'Create account', exact: true }).click();
    const allowed = await finish('Allow', first.state);
    assert.ok(allowed.searchParams.get('code'));
    assert.equal(allowed.searchParams.has('error'), false);
    const exchange = {
        grant_type: 'authorization_code', client_id: client.client_id, redirect_uri: redirect,
        code: allowed.searchParams.get('code'), code_verifier: first.verifier, resource: issuer + '/mcp',
    };
    const issued = await context.request.post(issuer + '/oauth/token', { form: exchange });
    assert.equal(issued.status(), 200);
    const credentials = await issued.json();
    assert.ok(credentials.access_token);
    assert.ok(credentials.id_token);
    const replay = await context.request.post(issuer + '/oauth/token', { form: exchange });
    assert.equal(replay.status(), 400);
    assert.equal((await replay.json()).error, 'invalid_grant');
    assert.equal(errors.length, 0, errors.join('\n').replace(/code=[^&\s]+/g, 'code=[redacted]'));
    const consumed = await context.request.get(issuer + '/oauth/consent');
    assert.equal(consumed.status(), 403);
    assert.ok((await consumed.text()).includes('Sign-in expired'));
    const second = await authorize();
    const denied = await finish('Deny', second.state);
    assert.equal(denied.searchParams.get('error'), 'access_denied');
    assert.equal(denied.searchParams.has('code'), false);
    assert.equal(errors.length, 0);
    const third = await authorize();
    await page.locator('form').evaluate((form, destination) => { form.action = destination; }, unrelated);
    const blocked = page.waitForEvent('console', { predicate: message => message.text().includes('form-action') });
    await page.getByRole('button', { name: 'Allow', exact: true }).click({ noWaitAfter: true });
    await blocked;
    assert.equal(new URL(page.url()).pathname, '/oauth/consent');
    assert.equal((await (await context.request.get(callbackOrigin + '/stats')).json()).unrelated, 0);
    assert.equal(consentPosts, 2);
    assert.equal(errors.length, 1);
    assert.ok(errors[0].includes('form-action'));
    await page.goto(issuer + '/oauth/consent');
    await finish('Allow', third.state);
    const visits = await (await context.request.get(callbackOrigin + '/stats')).json();
    assert.equal(visits.selected, 3);
    assert.equal(visits.unrelated, 0);
    assert.equal(errors.length, 1);
    console.log(JSON.stringify({
        crossOriginAllow: true, crossOriginDeny: true, singleClick: true, codeExchange: true,
        codeReplayRejected: true, consumedTransactionRejected: true,
        unselectedRegisteredCallbackBlocked: true, callbackRequests: visits.selected, unexpectedBrowserErrors: 0,
    }));
} finally {
    await browser.close();
}
