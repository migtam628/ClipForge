/* Secure environment management UI.
 * Does not place secrets in browser storage or download the .env file.
 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  if (location.pathname !== '/settings') return;
  let csrf = '';
  let revision = '';
  let original = new Map();
  const status = (message) => { $('admin-env-feedback').textContent = message; };
  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin', cache: 'no-store', ...options,
      headers: {...(options.headers || {}), ...(csrf ? {'x-clipforge-csrf': csrf} : {})},
    });
    let body = {};
    try { body = await response.json(); } catch { /* no JSON error */ }
    if (!response.ok) throw new Error(typeof body.detail === 'string' ?
      body.detail : 'Request failed (' + response.status + ')');
    return body;
  };
  const fieldRow = (entry) => {
    const row = document.createElement('label');
    row.className = 'environment-row';
    const top = document.createElement('span');
    top.className = 'environment-key';
    top.textContent = entry.key;
    const note = document.createElement('span');
    note.className = 'environment-description';
    note.textContent = entry.description + (entry.secret ? ' · protected secret' : '');
    const input = document.createElement('input');
    input.id = 'env-' + entry.key;
    input.dataset.key = entry.key;
    input.dataset.secret = String(entry.secret);
    input.type = entry.secret ? 'password' : 'text';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.disabled = !entry.editable;
    input.value = entry.secret ? '' : (entry.value || '');
    input.placeholder = entry.secret
      ? (entry.configured ? 'Configured — enter a replacement' : 'Not configured')
      : 'Not set';
    if (entry.secret) {
      input.setAttribute('aria-label', 'Replacement for ' + entry.key);
    }
    row.append(top, note, input);
    if (entry.secret && entry.editable && entry.configured) {
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.className = 'environment-clear';
      clear.textContent = 'Clear saved value';
      clear.addEventListener('click', (event) => {
        event.preventDefault();
        input.dataset.clear = 'true';
        input.value = '';
        input.placeholder = 'Will clear on save';
        clear.disabled = true;
      });
      input.addEventListener('input', () => {
        if (input.value) {
          delete input.dataset.clear;
          clear.disabled = false;
        }
      });
      row.append(clear);
    }
    original.set(entry.key, entry);
    return row;
  };
  const render = (entries) => {
    const parent = $('environment-fields');
    parent.replaceChildren();
    original = new Map();
    let section = '';
    for (const entry of entries) {
      if (entry.section !== section) {
        section = entry.section;
        const header = document.createElement('h3');
        header.textContent = section;
        parent.append(header);
      }
      parent.append(fieldRow(entry));
    }
  };
  const loadEnv = async () => {
    const result = await request('/api/admin/environment');
    revision = result.revision;
    $('admin-env-revision').textContent = 'Host .env revision: ' + revision;
    render(result.entries);
    $('admin-environment-panel').hidden = false;
    status('Connected to the restricted host configuration agent.');
  };
  const bootstrap = async () => {
    try {
      const session = await request('/api/admin/session');
      $('admin-login-form').hidden = !session.configured || session.authenticated;
      if (!session.configured) {
        $('admin-connection-status').textContent =
          'Administrator login is not configured. The environment editor is locked. Configure the admin password and host agent on the server first.';
        $('admin-environment-panel').hidden = true;
        return;
      }
      if (!session.authenticated) {
        $('admin-connection-status').textContent = 'Sign in to manage server configuration.';
        $('admin-environment-panel').hidden = true;
        return;
      }
      csrf = session.csrf;
      $('admin-connection-status').textContent = 'Administrator authenticated.';
      await loadEnv();
    } catch (error) {
      $('admin-connection-status').textContent = 'Environment editor unavailable.';
      $('admin-environment-panel').hidden = true;
      status(error.message);
    }
  };
  $('admin-login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const password = $('admin-password');
    try {
      const result = await request('/api/admin/login', {
        method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({password: password.value}),
      });
      csrf = result.csrf;
      password.value = '';
      await bootstrap();
    } catch (error) {
      password.value = '';
      status(error.message);
    }
  });
  $('admin-env-refresh').addEventListener('click', () => loadEnv().catch(
    (error) => status(error.message)));
  $('admin-logout').addEventListener('click', async () => {
    try {
      await request('/api/admin/logout', {method: 'POST'});
      csrf = '';
      $('admin-environment-panel').hidden = true;
      $('admin-login-form').hidden = false;
      status('Configuration session locked.');
    } catch (error) { status(error.message); }
  });
  $('admin-env-save').addEventListener('click', async () => {
    const changed = {};
    for (const [key, entry] of original) {
      if (!entry.editable) continue;
      const input = $('env-' + key);
      if (!input) continue;
      if (entry.secret) {
        if (input.dataset.clear === 'true') changed[key] = '';
        else if (input.value) changed[key] = input.value;
      } else if (input.value !== (entry.value || '')) {
        changed[key] = input.value;
      }
    }
    if (!Object.keys(changed).length) return status('No configuration changes to save.');
    const button = $('admin-env-save');
    button.disabled = true;
    try {
      await request('/api/admin/environment/validate', {
        method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({revision, changes: changed}),
      });
      if (!confirm('Save ' + Object.keys(changed).length +
          ' environment variable(s) to the host .env? Running containers will NOT restart.')) return;
      const result = await request('/api/admin/environment', {
        method: 'PATCH', headers: {'content-type': 'application/json'},
        body: JSON.stringify({revision, changes: changed}),
      });
      // No secret values are logged or stored after the request.
      for (const key of Object.keys(changed)) changed[key] = '';
      await loadEnv();
      status('Saved to host .env. Changes require a controlled restart when jobs are idle. Revision ' +
        result.revision + '.');
    } catch (error) { status(error.message); }
    finally { button.disabled = false; }
  });
  bootstrap();
})();
