const $ = (id) => document.getElementById(id);
let activeTab = 'url';
let polling = false;
let platforms = [];

$('url-tab').addEventListener('click', () => toggleTab('url'));
$('upload-tab').addEventListener('click', () => toggleTab('upload'));
$('create-button').addEventListener('click', createJob);
$('refresh').addEventListener('click', refresh);
$('file').addEventListener('change', () => {
  const file = $('file').files[0];
  if (file) document.querySelector('.drop-zone strong').textContent = file.name;
});

function toggleTab(tab) {
  activeTab = tab;
  for (const item of ['url', 'upload']) {
    $(item + '-area').hidden = item !== tab;
    $(item + '-tab').classList.toggle('active', item === tab);
    $(item + '-tab').setAttribute('aria-selected', String(item === tab));
  }
}

function notify(text) {
  $('message').textContent = text;
  $('message').hidden = !text;
}

async function createJob() {
  notify('');
  if (!$('rights').checked) return notify('Please confirm your rights to clip this content.');
  const options = {
    clip_seconds: Number($('clip-seconds').value),
    max_clips: Number($('max-clips').value),
    aspect_ratio: $('aspect-ratio').value,
    selection_mode: $('selection-mode').value,
    caption_style: $('caption-style').value,
    effect: $('effect').value,
    framing: $('framing').value,
    rights_confirmed: true
  };
  let request;
  if (activeTab === 'url') {
    const url = $('url').value.trim();
    if (!url) return notify('Enter a source video URL.');
    request = {
      headers: {'Content-Type': 'application/json'},
      method: 'POST', body: JSON.stringify({...options, url})
    };
  } else {
    const file = $('file').files[0];
    if (!file) return notify('Select a video file to upload.');
    const data = new FormData();
    for (const [k, v] of Object.entries(options)) data.set(k, String(v));
    data.set('file', file);
    request = {method: 'POST', body: data};
  }
  $('create-button').disabled = true;
  try {
    const response = await fetch(activeTab === 'url' ? '/api/jobs' : '/api/uploads', request);
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not queue video');
    $('url').value = '';
    $('file').value = '';
    $('rights').checked = false;
    await refresh();
    $('library').scrollIntoView({behavior: 'smooth'});
  } catch (err) {
    notify(err.message);
  } finally {
    $('create-button').disabled = false;
  }
}

function el(tag, cls, content) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (content != null) node.textContent = String(content);
  return node;
}

function card(job) {
  const wrapper = el('article', 'job');
  const head = el('div', 'job-head');
  const meta = el('div');
  meta.append(el('h3', '', job.label), el('p', '', `${new Date(job.created_at).toLocaleString()} · ${job.options.aspect_ratio} · ${job.options.clip_seconds}s · ${job.options.selection_mode === 'ai' ? 'AI selected' : 'Audio-energy selected'}`));
  head.append(meta, el('span', `job-status ${job.status === 'failed' ? 'failed' : ''}`, job.status));
  wrapper.append(head);
  if (job.status === 'failed') wrapper.append(el('p', 'error', job.error || 'This job failed.'));
  if (job.status !== 'completed' && job.status !== 'failed') {
    const bar = el('div', 'progress-bg');
    const fill = el('div', 'progress-fill');
    fill.style.width = `${job.progress}%`;
    bar.append(fill);
    wrapper.append(bar);
  }
  if (job.clips.length) {
    const actions = el('div', 'job-actions');
    actions.append(el('span', '', `${job.clips.length} clips generated`));
    if (job.status === 'completed') {
      const download = el('a', '', '⇩ Download all (.zip)');
      download.href = `/api/jobs/${job.id}/export`;
      actions.append(download);
    }
    wrapper.append(actions);
    const grid = el('div', 'clip-grid');
    for (const clip of job.clips) {
      const item = el('div', 'clip');
      const video = el('video');
      video.controls = true;
      video.preload = 'none';
      video.src = `/api/jobs/${job.id}/clips/${clip.filename}`;
      if (clip.thumbnail) video.poster = `/api/jobs/${job.id}/clips/${clip.thumbnail}`;
      const description = el('div', 'clip-info');
      description.append(el('strong', '', `Clip ${String(clip.number).padStart(2, '0')}`), el('span', '', `${Math.floor(clip.start / 60)}:${String(Math.floor(clip.start % 60)).padStart(2, '0')} · ${clip.duration}s`));
      const link = el('a', '', '↓ Save MP4');
      link.href = video.src;
      link.download = clip.filename;
      description.append(link);
      if (clip.title) description.append(el('p', 'clip-ai-title', clip.title));
      if (clip.reason) description.append(el('p', 'clip-ai-reason', clip.reason));
      if (clip.captions_added) description.append(el('span', 'clip-tag', 'CC · Captions burned in'));
      if (job.status === 'completed') {
        const available = platforms.filter((p) => p.configured);
        if (available.length) {
          const publishButton = el('button', 'publish-button', '↗ Send to social platform');
          const panel = el('div', 'publish-panel');
          panel.hidden = true;
          const platformField = el('select');
          for (const p of available) {
            const option = el('option', '', p.name);
            option.value = p.id;
            platformField.append(option);
          }
          const titleField = el('input');
          titleField.maxLength = 100;
          titleField.value = clip.title || `Clip ${clip.number}`;
          titleField.placeholder = 'Post title';
          const captionField = el('textarea');
          captionField.maxLength = 2200;
          captionField.rows = 2;
          captionField.placeholder = 'Description / hashtags (optional)';
          const visibility = el('select');
          for (const v of ['private', 'unlisted', 'public']) {
            const opt = el('option', '', `YouTube: ${v}`);
            opt.value = v;
            visibility.append(opt);
          }
          const guidance = el('small', 'publish-guidance');
          const send = el('button', 'publish-submit', 'Approve and send');
          function updateGuidance() {
            const isYoutube = platformField.value === 'youtube';
            visibility.hidden = !isYoutube;
            guidance.textContent = platformField.value === 'tiktok'
              ? 'TikTok receives a draft. Finish posting in the TikTok app.'
              : 'This sends your video to the selected account via an authorized API.';
          }
          platformField.addEventListener('change', updateGuidance);
          updateGuidance();
          send.addEventListener('click', async () => {
            if (!titleField.value.trim()) return notify('Enter a post title.');
            const service = available.find((p) => p.id === platformField.value);
            if (!confirm(`Send ${clip.filename} to ${service.name}? This will transfer video outside ClipForge.`)) return;
            send.disabled = true;
            try {
              const res = await fetch(`/api/jobs/${job.id}/publish`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({filename: clip.filename, platform: platformField.value,
                  title: titleField.value.trim(), description: captionField.value,
                  visibility: visibility.value, approved: true})
              });
              const outcome = await res.json();
              if (!res.ok) throw new Error(outcome.detail || 'Publishing request failed');
              notify('Accepted. Check the publishing status below; the platform may still be processing.');
              panel.hidden = true;
              await refresh();
            } catch (err) { notify(err.message); }
            finally { send.disabled = false; }
          });
          panel.append(platformField, titleField, captionField, visibility, guidance, send);
          publishButton.addEventListener('click', () => {
            panel.hidden = !panel.hidden;
          });
          description.append(publishButton, panel);
        } else {
          description.append(el('small', 'publish-guidance',
            'Social publishing is available after server credentials are configured. Download MP4 meanwhile.'));
        }
      }
      const posts = (job.publications || []).filter((p) => p.filename === clip.filename);
      for (const post of posts) {
        const status = el('p', 'publication-status',
          `${post.platform}: ${post.state}${post.error ? ' · ' + post.error : ''}`);
        if (post.url) {
          const linkPost = el('a', '', 'View on platform ↗');
          linkPost.href = post.url;
          linkPost.target = '_blank';
          linkPost.rel = 'noopener noreferrer';
          status.append(' ', linkPost);
        }
        description.append(status);
      }
      item.append(video, description);
      grid.append(item);
    }
    wrapper.append(grid);
  }
  if (job.status === 'completed' || job.status === 'failed') {
    const actions = el('div', 'job-actions');
    const remove = el('button', '', 'Delete job');
    remove.addEventListener('click', async () => {
      if (!confirm('Delete this job and all generated files?')) return;
      const res = await fetch(`/api/jobs/${job.id}`, {method: 'DELETE'});
      if (res.ok) refresh();
    });
    actions.append(remove);
    wrapper.append(actions);
  }
  return wrapper;
}

async function refresh() {
  if (polling) return;
  polling = true;
  try {
    const [response, platformsResponse] = await Promise.all([
      fetch('/api/jobs', {cache: 'no-store'}),
      fetch('/api/publishing/platforms', {cache: 'no-store'})
    ]);
    if (platformsResponse.ok) platforms = await platformsResponse.json();
    if (!response.ok) throw new Error('Cannot reach ClipForge server');
    const jobs = await response.json();
    if (!jobs.length) {
      $('jobs').replaceChildren(el('div', 'empty'));
      $('jobs').firstChild.append(el('div', 'empty-graphic', '▤'), el('strong', '', 'No clips yet'), el('span', '', 'Your generated clips will show up here.'));
    } else {
      $('jobs').replaceChildren(...jobs.map(card));
    }
  } catch (err) {
    notify(err.message);
  } finally {
    polling = false;
  }
}

refresh();
setInterval(() => {
  // Do not recreate video players while a clip is playing.
  if (![...document.querySelectorAll('video')].some((v) => !v.paused)
    && ![...document.querySelectorAll('.publish-panel')].some((panel) => !panel.hidden)) refresh();
}, 4000);

(async () => {
  try {
    const res = await fetch('/api/ai/status');
    if (!res.ok) return;
    const status = await res.json();
    const notice = $('ai-notice');
    if (!status.whisper_installed || !status.ollama_configured) {
      notice.textContent = 'AI is not configured yet. Start Docker with --profile ai, pull the model, and install Whisper. Audio-energy mode remains available.';
    } else {
      notice.textContent = `AI engine: Whisper + ${status.model}. First transcription downloads speech models; long videos use substantial CPU time.`;
    }
  } catch (_) { /* Optional capability banner */ }
})();
