const $ = (id) => document.getElementById(id);
let activeTab = 'url';
let polling = false;
let platforms = [];
let publishingPresets = [];
const validPages = ['studio', 'library', 'publishing', 'activity', 'settings'];
const currentPage = validPages.includes(location.pathname.slice(1))
  ? location.pathname.slice(1) : 'studio';
const labels = {studio: 'Studio', library: 'Clip Library',
  publishing: 'Publishing', activity: 'Activity', settings: 'Settings'};
$('page-crumb').textContent = labels[currentPage];
document.title = 'ClipForge · ' + labels[currentPage];
for (const page of validPages) {
  $('view-' + page).hidden = page !== currentPage;
}
for (const item of document.querySelectorAll('.nav-link[data-page]')) {
  const active = item.dataset.page === currentPage;
  item.classList.toggle('selected', active);
  if (active) item.setAttribute('aria-current', 'page');
}

$('url-tab').addEventListener('click', () => toggleTab('url'));
$('upload-tab').addEventListener('click', () => toggleTab('upload'));
$('create-button').addEventListener('click', createJob);
for (const button of document.querySelectorAll('[data-refresh]')) {
  button.addEventListener('click', refresh);
}
function chosenVideoFiles() {
  const folderFiles = $('folder').files.length ? [...$('folder').files] : [...$('file').files];
  return folderFiles.filter((f) => /\.(mp4|mov|mkv|webm)$/i.test(f.name));
}
function updateUploadSelection() {
  const sources = chosenVideoFiles();
  document.querySelector('.drop-zone strong').textContent =
    sources.length ? sources.length + ' video source' + (sources.length === 1 ? '' : 's') + ' selected' : 'Choose one or many videos';
  $('batch-selection-hint').textContent =
    sources.length > 1 ? sources.length + ' videos selected. ClipForge processes them sequentially, one checkpointed job per source.' :
    'Videos are queued one at a time for CPU-friendly processing. Each keeps its own retry checkpoints.';
}
$('file').addEventListener('change', () => { $('folder').value = ''; updateUploadSelection(); });
$('folder').addEventListener('change', () => { $('file').value = ''; updateUploadSelection(); });
$('max-clips').addEventListener('change', updateCustomCount);
$('narration').addEventListener('change', updateNarrationNotice);
$('settings-form').addEventListener('submit', savePreferences);

function updateCustomCount() {
  $('custom-max-clips').hidden = $('max-clips').value !== 'custom';
}

function updateNarrationNotice() {
  const mode = $('narration').value;
  const note = $('narration-notice');
  if (mode === 'narrated') {
    note.textContent = 'Voiceover uses ElevenLabs and may incur usage charges. Original audio will be quieter under the commentator. Configure a voice in Settings.';
  } else if (mode === 'script') {
    note.textContent = 'Generates a grounded AI script you can read or copy. It does not create voice audio.';
  } else {
    note.textContent = 'Use original video audio. No AI script or external text-to-speech charge.';
  }
}


function toggleTab(tab) {
  activeTab = tab;
  for (const item of ['url', 'upload']) {
    $(item + '-area').hidden = item !== tab;
    $(item + '-tab').classList.toggle('active', item === tab);
    $(item + '-tab').setAttribute('aria-selected', String(item === tab));
  }
}

function notify(text) {
  const notice = currentPage === 'studio' ? $('message') : toast();
  notice.textContent = text;
  notice.hidden = !text;
}

function toast() {
  let node = $('global-toast');
  if (!node) {
    node = el('div', 'global-toast');
    node.id = 'global-toast';
    node.setAttribute('role', 'status');
    document.body.append(node);
  }
  return node;
}


async function createJob() {
  notify('');
  if (!$('rights').checked) return notify('Please confirm your rights to clip this content.');
  const options = {
    clip_seconds: Number($('clip-seconds').value),
    max_clips: Number($('max-clips').value === 'custom' ? $('custom-max-clips').value : $('max-clips').value),
    aspect_ratio: $('aspect-ratio').value,
    selection_mode: $('selection-mode').value,
    caption_style: $('caption-style').value,
    effect: $('effect').value,
    framing: $('framing').value,
    hook_style: $('hook-style').value,
    narration: $('narration').value,
    narration_style: $('narration-style').value,
    narration_language: $('narration-language').value,
    rights_confirmed: true
  };
  if (!Number.isInteger(options.max_clips) ||
      options.max_clips < 1 || options.max_clips > 100) {
    return notify('Choose between 1 and 100 clips.');
  }
  let request;
  let endpoint;
  if (activeTab === 'url') {
    const multiline = $('url-list').value.trim();
    const urls = [...new Set((multiline ? multiline.split(/\r?\n/) : [$('url').value])
      .map((x) => x.trim()).filter(Boolean))];
    if (!urls.length) return notify('Enter a video URL or multiple authorized URLs.');
    if (urls.length > 100) return notify('Maximum 100 source URLs in a batch.');
    if (options.narration === 'narrated' && urls.length > 1 &&
        !confirm('Narrating all ' + urls.length + ' sources can incur ElevenLabs charges. Approve this batch?')) return;
    endpoint = urls.length > 1 ? '/api/jobs/batch' : '/api/jobs';
    request = {
      headers: {'Content-Type': 'application/json'}, method: 'POST',
      body: JSON.stringify(urls.length > 1
        ? {...options, urls, voice_spend_approved: options.narration === 'narrated'}
        : {...options, url: urls[0]})
    };
  } else {
    const files = chosenVideoFiles();
    if (!files.length) return notify('Select MP4, MOV, MKV or WEBM files first.');
    if (files.length > 100) return notify('Maximum 100 source videos in a batch.');
    if (files.some((file) => file.size > 2 * 1024 ** 3) ||
        files.reduce((sum, file) => sum + file.size, 0) > 8 * 1024 ** 3)
      return notify('Each file must be at most 2 GB; batch total at most 8 GB.');
    if (options.narration === 'narrated' && files.length > 1 &&
        !confirm('Narrating all ' + files.length + ' sources can incur ElevenLabs charges. Approve this batch?')) return;
    const data = new FormData();
    for (const [k, v] of Object.entries(options)) data.set(k, String(v));
    endpoint = files.length > 1 ? '/api/uploads/batch' : '/api/uploads';
    if (files.length > 1) {
      data.set('voice_spend_approved', String(options.narration === 'narrated'));
      files.forEach((file) => data.append('files', file, file.name));
    } else data.set('file', files[0]);
    request = {method: 'POST', body: data};
  }
  $('create-button').disabled = true;
  try {
    const response = await fetch(endpoint, request);
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not queue video');
    $('url').value = '';
    $('url-list').value = '';
    $('file').value = '';
    $('folder').value = '';
    $('rights').checked = false;
    location.assign('/activity');
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

function buildClipEditor(job, clip) {
  const editor = el('details', 'clip-editor');
  editor.append(el('summary', '', '✦ Refine this clip'));
  const form = el('form', 'clip-editor-form');
  const fields = el('div', 'clip-edit-fields');

  function control(labelText, node) {
    const field = el('label', 'clip-edit-field');
    field.append(el('span', '', labelText), node);
    fields.append(field);
    return node;
  }
  function dropdown(labelText, options, selected) {
    const select = el('select');
    for (const [value, label] of options) {
      const option = el('option', '', label);
      option.value = value;
      select.append(option);
    }
    select.value = selected;
    return control(labelText, select);
  }

  const title = el('input');
  title.value = clip.title || '';
  title.maxLength = 100;
  title.placeholder = 'An irresistible opening hook';
  control('Headline / title', title);

  const start = el('input');
  start.type = 'number';
  start.min = '0';
  start.max = String(job.duration || 999999);
  start.step = '0.1';
  start.value = String(clip.start);
  start.required = true;
  control('Start time (seconds)', start);

  const duration = el('input');
  duration.type = 'number';
  duration.min = '3';
  duration.max = '120';
  duration.step = '0.1';
  duration.value = String(clip.duration);
  duration.required = true;
  control('Length (seconds)', duration);

  const effect = dropdown('Color treatment', [
    ['none', 'Natural'], ['pop', 'Vibrant pop'],
    ['cinema', 'Cinematic'], ['noir', 'Black & white']
  ], clip.effect || job.options.effect);

  const framing = dropdown('Framing', [
    ['smart', 'Smart · follow faces'], ['blur', 'Blurred background'],
    ['crop', 'Centered crop'], ['pad', 'Keep full frame']
  ], clip.framing || job.options.framing);

  const captions = dropdown('Captions', [
    ['karaoke', 'Word-aligned karaoke'], ['pop', 'Animated pop words'],
    ['bold', 'Bold yellow'], ['classic', 'Classic white'],
    ['neon', 'Neon pink'], ['none', 'No captions']
  ], clip.caption_style || (clip.captions_added ? job.options.caption_style : 'none'));

  const hook = dropdown('Opening headline', [
    ['headline', 'Show first 3 seconds'], ['none', 'No headline']
  ], clip.hook_style || 'none');

  const script = el('textarea');
  script.rows = 3;
  script.maxLength = 1200;
  script.value = clip.script || '';
  script.placeholder = 'Optional custom narration script';
  control('Commentator script (optional)', script);

  const voiceRow = el('label', 'clip-edit-consent');
  const voice = el('input');
  voice.type = 'checkbox';
  voiceRow.append(voice, el('span', '',
    'Generate new spoken commentary with ElevenLabs (external API, may incur a charge)'));
  const approvalRow = el('label', 'clip-edit-consent');
  const approval = el('input');
  approval.type = 'checkbox';
  approvalRow.append(approval, el('span', '',
    'I approve the external text-to-speech request and possible usage charge.'));
  approvalRow.hidden = true;
  voice.addEventListener('change', () => { approvalRow.hidden = !voice.checked; });

  const explain = el('p', 'helper-note',
    'Edits use the original video. A new MP4 is rendered; earlier versions stay available. Narration is off by default to avoid unexpected API charges.');
  const actions = el('div', 'clip-edit-actions');
  const save = el('button', 'small-action primary-action', 'Render revised clip');
  save.type = 'submit';
  if (['queued', 'rendering'].includes(clip.revision_state)) save.disabled = true;
  actions.append(save);
  form.append(fields, voiceRow, approvalRow, explain, actions);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const begin = Number(start.value);
    const span = Number(duration.value);
    if (!Number.isFinite(begin) || !Number.isFinite(span) ||
        span < 3 || span > 120 || begin < 0 ||
        begin + span > Number(job.duration || 0) + 0.05)
      return notify('Trim needs to fit within the original video (3–120 seconds).');
    if (voice.checked && (!approval.checked || !script.value.trim()))
      return notify('Enter a script and approve ElevenLabs usage to create a voiceover.');
    const settings = {
      start: begin, duration: span, title: title.value.trim(),
      effect: effect.value, framing: framing.value,
      caption_style: captions.value, hook_style: hook.value,
      script: script.value.trim(), voiceover: voice.checked,
      voiceover_approved: approval.checked && voice.checked
    };
    save.disabled = true;
    try {
      const url = '/api/jobs/' + job.id + '/clips/' + clip.number + '/edit';
      const response = await fetch(url, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(settings)
      });
      const result = await response.json();
      if (!response.ok)
        throw new Error(typeof result.detail === 'string'
          ? result.detail : 'Could not queue clip edit');
      notify('Clip revision queued. The previous video stays playable.');
      editor.open = false;
      await refresh();
    } catch (error) {
      notify(error.message);
      save.disabled = false;
    }
  });
  editor.append(form);
  if (clip.revision_history && clip.revision_history.length) {
    const versions = el('div', 'clip-edit-history');
    versions.append(el('strong', '', 'Earlier versions'));
    for (const prior of clip.revision_history) {
      if (!prior.filename) continue;
      const link = el('a', '', '↓ ' + prior.filename);
      link.href = '/api/jobs/' + job.id + '/clips/' + prior.filename;
      link.download = prior.filename;
      versions.append(link);
    }
    editor.append(versions);
  }
  return editor;
}

function card(job, mode = currentPage) {
  const wrapper = el('article', 'job');
  const head = el('div', 'job-head');
  const meta = el('div');
  meta.append(el('h3', '', job.label), el('p', '', `${new Date(job.created_at).toLocaleString()} · ${job.options.aspect_ratio} · ${job.options.clip_seconds}s · ${job.options.selection_mode === 'ai' ? 'AI selected' : 'Audio-energy selected'}${job.resume_count ? ' · resumed ' + job.resume_count + 'x' : ''}`));
  head.append(meta, el('span', `job-status ${job.status === 'failed' ? 'failed' : ''}`, job.status));
  wrapper.append(head);
  if (job.batch_id)
    wrapper.append(el('span', 'clip-tag', 'Batch source ' + job.batch_index + '/' + job.batch_total));
  if (job.status === 'failed') wrapper.append(el('p', 'error', job.error || 'This job failed.'));
  if (job.status !== 'completed' && job.status !== 'failed') {
    const bar = el('div', 'progress-bg');
    const fill = el('div', 'progress-fill');
    fill.style.width = `${job.progress}%`;
    bar.append(fill);
    wrapper.append(bar);
  }
  if (job.clips.length && mode !== 'activity') {
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
      if (clip.voiceover_added) description.append(el('span', 'clip-tag', '♬ Commentator voiceover'));
      if (clip.hook_style === 'headline') description.append(el('span', 'clip-tag', '✦ Opening hook'));
      if (clip.face_tracking) description.append(el('span', 'clip-tag', '◉ Face-aware crop'));
      else if (clip.framing === 'smart') description.append(el('span', 'clip-tag', 'Centered fallback'));
      if (clip.revision_count) description.append(el('span', 'clip-tag', 'Revision ' + clip.revision_count));
      if (clip.revision_state === 'queued' || clip.revision_state === 'rendering')
        description.append(el('p', 'editor-progress', '⌁ Rendering your edited version…'));
      if (clip.revision_state === 'failed')
        description.append(el('p', 'error', clip.revision_error || 'Clip revision failed. The previous version is still available.'));
      if (clip.script) {
        const scriptDetails = el('details', 'script-details');
        const toggle = el('summary', '', 'Read AI commentary script');
        const scriptText = el('p', 'script-copy', clip.script);
        const copyButton = el('button', 'small-action', 'Copy script');
        copyButton.type = 'button';
        copyButton.addEventListener('click', () => {
          navigator.clipboard.writeText(clip.script)
            .then(() => notify('Commentary script copied.'))
            .catch(() => notify('Could not copy script.'));
        });
        scriptDetails.append(toggle, scriptText, copyButton);
        description.append(scriptDetails);
      }
      if (job.status === 'completed' && mode === 'library') {
        description.append(buildClipEditor(job, clip));
      }
      if (job.status === 'completed' && mode === 'publishing') {
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

          const scheduleButton = el('button', 'publish-button schedule-button',
            '◷ Schedule to multiple platforms');
          const schedulePanel = el('div', 'publish-panel schedule-panel');
          schedulePanel.hidden = true;

          const platformBoxes = el('div', 'schedule-platforms');
          for (const p of available) {
            const option = el('label', 'schedule-platform-option');
            const input = el('input');
            input.type = 'checkbox';
            input.value = p.id;
            option.append(input, el('span', '', p.name));
            platformBoxes.append(option);
          }
          const scheduledTitle = el('input');
          scheduledTitle.maxLength = 100;
          scheduledTitle.value = clip.title || 'Clip ' + clip.number;
          scheduledTitle.placeholder = 'Publication title';

          const scheduledDescription = el('textarea');
          scheduledDescription.rows = 2;
          scheduledDescription.maxLength = 2200;
          scheduledDescription.placeholder = 'Optional description / hashtags';
          const scheduledVisibility = el('select');
          for (const v of ['private', 'unlisted', 'public']) {
            const option = el('option', '', 'YouTube: ' + v);
            option.value = v;
            scheduledVisibility.append(option);
          }
          const scheduledTime = el('input');
          scheduledTime.type = 'datetime-local';
          scheduledTime.required = true;
          scheduledTime.setAttribute('aria-label', 'Scheduled local date and time');
          const guidance = el('p', 'helper-note',
            'Approved uploads run at approximately the selected local time. TikTok receives an inbox draft, not a public post. Already-published files are not replaced by later edits.');

          const presetSelect = el('select');
          presetSelect.append(el('option', '', 'Choose a saved preset (optional)'));
          for (const preset of publishingPresets) {
            const option = el('option', '', preset.name);
            option.value = preset.name;
            presetSelect.append(option);
          }
          const selectedPlatforms = () => [...platformBoxes.querySelectorAll('input:checked')]
            .map((input) => input.value);
          presetSelect.addEventListener('change', () => {
            const preset = publishingPresets.find((item) => item.name === presetSelect.value);
            if (!preset) return;
            for (const input of platformBoxes.querySelectorAll('input'))
              input.checked = preset.platforms.includes(input.value);
            scheduledDescription.value = preset.description || '';
            scheduledVisibility.value = preset.visibility || 'private';
          });

          const savePreset = el('button', 'small-action', 'Save platform preset');
          savePreset.type = 'button';
          savePreset.addEventListener('click', async () => {
            const selected = selectedPlatforms();
            if (!selected.length) return notify('Choose at least one platform.');
            const name = prompt('Name this reusable platform preset (max 50 characters):');
            if (!name || !name.trim()) return;
            try {
              const res = await fetch('/api/publishing/presets', {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  name: name.trim(), platforms: selected,
                  description: scheduledDescription.value,
                  visibility: scheduledVisibility.value
                })
              });
              const data = await res.json();
              if (!res.ok) throw new Error(typeof data.detail === 'string'
                ? data.detail : 'Could not save preset');
              publishingPresets = publishingPresets.filter((p) => p.name !== data.name);
              publishingPresets.push(data);
              notify('Publishing preset saved. Available on the next refresh.');
            } catch (err) { notify(err.message); }
          });

          const scheduleSubmit = el('button', 'publish-submit', 'Approve and schedule uploads');
          scheduleSubmit.type = 'button';
          scheduleSubmit.addEventListener('click', async () => {
            const selected = selectedPlatforms();
            if (!selected.length) return notify('Select one or more connected platforms.');
            if (!scheduledTitle.value.trim()) return notify('Enter a title.');
            if (!scheduledTime.value) return notify('Choose a scheduled date and time.');
            const when = new Date(scheduledTime.value);
            if (Number.isNaN(when.getTime()) || when.getTime() < Date.now() + 60000)
              return notify('Schedule at least one minute into the future.');
            const names = available.filter((p) => selected.includes(p.id)).map((p) => p.name).join(', ');
            if (!confirm('Approve ' + clip.filename + ' for ' + names + ' on ' +
              when.toLocaleString() + '? The server will send the video automatically.')) return;
            scheduleSubmit.disabled = true;
            try {
              const response = await fetch('/api/jobs/' + job.id + '/schedule', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  filename: clip.filename, platforms: selected,
                  title: scheduledTitle.value.trim(), description: scheduledDescription.value,
                  visibility: scheduledVisibility.value, scheduled_at: when.toISOString(),
                  approved: true
                })
              });
              const result = await response.json();
              if (!response.ok) throw new Error(
                typeof result.detail === 'string' ? result.detail : 'Schedule was rejected');
              notify('Scheduled ' + selected.length + ' approved platform transfers.');
              schedulePanel.hidden = true;
              await refresh();
            } catch (err) { notify(err.message); }
            finally { scheduleSubmit.disabled = false; }
          });
          schedulePanel.append(
            el('label', '', 'Publishing preset'), presetSelect,
            el('label', '', 'Select destinations'), platformBoxes,
            el('label', '', 'Title'), scheduledTitle,
            el('label', '', 'Description'), scheduledDescription,
            el('label', '', 'YouTube visibility'), scheduledVisibility,
            el('label', '', 'Scheduled time (your local timezone)'), scheduledTime,
            guidance, savePreset, scheduleSubmit,
          );
          scheduleButton.addEventListener('click', () => {
            schedulePanel.hidden = !schedulePanel.hidden;
          });
          description.append(scheduleButton, schedulePanel);
        } else {
          description.append(el('small', 'publish-guidance',
            'Connect a platform in your server configuration to enable approved uploads.'));
        }
      } else if (job.status === 'completed' && mode === 'library') {
        const shareLink = el('a', 'library-publish-link', '↗ Review in Publishing');
        shareLink.href = '/publishing';
        description.append(shareLink);
      }
      const posts = (job.publications || []).filter((p) => p.filename === clip.filename);
      for (const post of posts) {
        const status = el('p', 'publication-status',
          `${post.platform}: ${post.state}${post.scheduled_at ? ' · ' + new Date(post.scheduled_at).toLocaleString() : ''}${post.error ? ' · ' + post.error : ''}`);
        if (post.url) {
          const linkPost = el('a', '', 'View on platform ↗');
          linkPost.href = post.url;
          linkPost.target = '_blank';
          linkPost.rel = 'noopener noreferrer';
          status.append(' ', linkPost);
        }
        if (post.state === 'scheduled') {
          const cancel = el('button', 'small-action cancel-schedule', 'Cancel scheduled upload');
          cancel.type = 'button';
          cancel.addEventListener('click', async () => {
            if (!confirm('Cancel the scheduled transfer to ' + post.platform + '?')) return;
            const res = await fetch('/api/jobs/' + job.id + '/publications/' +
                                    post.id + '/cancel', {method: 'POST'});
            if (!res.ok) return notify('Cannot cancel; it may already be sending.');
            notify('Scheduled upload canceled.');
            await refresh();
          });
          status.append(' ', cancel);
        }
        if (post.state === 'needs_review' || post.state === 'failed') {
          const retry = el('button', 'small-action cancel-schedule', 'Review and retry');
          retry.type = 'button';
          retry.addEventListener('click', async () => {
            if (!confirm('First check ' + post.platform + ' for an existing upload. Continue only if you accept the risk of a duplicate post.')) return;
            if (!confirm('I checked the platform and approve retrying this upload, even if it creates a duplicate.')) return;
            const response = await fetch('/api/jobs/' + job.id + '/publications/' +
              post.id + '/reapprove', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({approved: true, duplicate_checked: true})
              });
            if (!response.ok) return notify('Retry was rejected. Check server configuration.');
            notify('Approved retry submitted to publishing queue.');
            await refresh();
          });
          status.append(' ', retry);
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
    if (job.status === 'failed') {
      const resume = el('button', 'resume-job', '↻ Resume job');
      resume.title = 'Reuse saved source, transcript, scores and completed clips';
      resume.addEventListener('click', async () => {
        resume.disabled = true;
        try {
          const response = await fetch('/api/jobs/' + job.id + '/resume', {method: 'POST'});
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || 'Could not resume job');
          notify('Job queued for recovery. Completed stages will be reused.');
          await refresh();
        } catch (err) { notify(err.message); resume.disabled = false; }
      });
      actions.append(resume);
    }
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
  if (!['library', 'publishing', 'activity'].includes(currentPage) || polling) return;
  polling = true;
  const target = $(currentPage === 'library' ? 'jobs-library' :
                   currentPage === 'publishing' ? 'jobs-publishing' : 'jobs-activity');
  try {
    const [response, platformsResponse, presetsResponse] = await Promise.all([
      fetch('/api/jobs', {cache: 'no-store'}),
      fetch('/api/publishing/platforms', {cache: 'no-store'}),
      currentPage === 'publishing' ? fetch('/api/publishing/presets', {cache: 'no-store'}) : Promise.resolve(null)
    ]);
    if (platformsResponse.ok) platforms = await platformsResponse.json();
    if (presetsResponse && presetsResponse.ok)
      publishingPresets = await presetsResponse.json();
    if (!response.ok) throw new Error('Cannot reach ClipForge server');
    const jobs = await response.json();
    const shown = currentPage === 'library'
      ? jobs.filter((job) => job.clips.length > 0 || job.status === 'completed')
      : currentPage === 'publishing'
        ? jobs.filter((job) => job.status === 'completed')
        : jobs;
    target.replaceChildren();
    if (!shown.length) {
      const empty = el('div', 'empty');
      empty.append(el('div', 'empty-graphic', '▤'),
        el('strong', '', currentPage === 'activity' ? 'No processing jobs yet' :
                          currentPage === 'publishing' ? 'No clips ready to publish' :
                          'Your clip library is empty'),
        el('span', '', currentPage === 'activity' ? 'Start a video in Studio to see it here.' :
          'Create clips in Studio to populate this page.'));
      target.append(empty);
    } else {
      target.append(...shown.map((job) => card(job, currentPage)));
    }
    if (currentPage === 'library') {
      const count = shown.reduce((sum, job) => sum + job.clips.length, 0);
      $('library-summary').textContent = count + ' clips across ' + shown.length + ' jobs';
    }
    if (currentPage === 'activity') {
      const working = jobs.filter((job) => !['failed', 'completed'].includes(job.status));
      const completed = jobs.filter((job) => job.status === 'completed');
      const failed = jobs.filter((job) => job.status === 'failed');
      const parent = $('activity-stats');
      parent.replaceChildren();
      for (const [value, label] of [
        [working.length, 'In progress'], [completed.length, 'Completed'],
        [failed.length, 'Needs attention'], [jobs.length, 'Total jobs']
      ]) {
        const item = el('div', 'stat-item');
        item.append(el('strong', '', String(value)), el('span', '', label));
        parent.append(item);
      }
    }
    if (currentPage === 'activity') {
      const batches = new Map();
      for (const job of jobs) {
        if (!job.batch_id) continue;
        if (!batches.has(job.batch_id))
          batches.set(job.batch_id, {total: job.batch_total || 0, completed: 0, failed: 0});
        const record = batches.get(job.batch_id);
        if (job.status === 'completed') record.completed++;
        if (job.status === 'failed') record.failed++;
      }
      const group = $('activity-batches');
      group.replaceChildren();
      for (const [id, record] of batches.entries()) {
        const item = el('div', 'batch-summary-item');
        item.append(el('strong', '', 'Batch ' + id.slice(0, 8)),
                    el('span', '', record.completed + '/' + record.total + ' completed · ' +
                       record.failed + ' need attention'));
        group.append(item);
      }
    }
    if (currentPage === 'publishing') {
      renderPlatforms();
      const scheduled = jobs.reduce((count, job) => count +
        (job.publications || []).filter((p) => p.state === 'scheduled').length, 0);
      $('publishing-scheduled-count').textContent =
        scheduled + ' approved future platform transfer' + (scheduled === 1 ? '' : 's');
    }
  } catch (err) {
    notify(err.message);
  } finally {
    polling = false;
  }
}

function renderPlatforms() {
  const group = $('platform-connections');
  if (!group) return;
  group.replaceChildren();
  for (const p of platforms) {
    const item = el('div', 'connection-card');
    item.append(el('strong', '', p.name),
                el('span', p.configured ? 'connected' : 'not-connected',
                   p.configured ? 'Configured' : 'Not connected'));
    group.append(item);
  }
}

const settingsIds = {
  clip_seconds: 'clip-seconds',
  max_clips: 'max-clips',
  aspect_ratio: 'aspect-ratio',
  selection_mode: 'selection-mode',
  caption_style: 'caption-style',
  effect: 'effect',
  framing: 'framing',
  hook_style: 'hook-style',
  narration: 'narration',
  narration_style: 'narration-style',
  narration_language: 'narration-language',
};

function applyDefaults(defaults, prefix) {
  for (const [key, field] of Object.entries(settingsIds)) {
    const item = $(prefix + field);
    if (!item || defaults[key] === undefined) continue;
    if (!prefix && key === 'max_clips') {
      const amount = String(defaults[key]);
      if (![...item.options].some((opt) => opt.value === amount)) {
        item.value = 'custom';
        $('custom-max-clips').value = amount;
      } else item.value = amount;
      updateCustomCount();
    } else item.value = String(defaults[key]);
  }
}

async function loadSettings() {
  try {
    const response = await fetch('/api/settings', {cache: 'no-store'});
    if (!response.ok) throw new Error('Could not load settings');
    const settings = await response.json();
    if (currentPage === 'studio') {
      applyDefaults(settings.defaults, '');
      $('ai-notice').textContent = settings.ai.whisper_installed &&
          settings.ai.ollama_configured
        ? 'AI: Whisper + ' + settings.ai.model + '. Local scoring can take longer on CPU-only servers.'
        : 'AI is not fully configured. You can still use Audio-energy selection.';
      updateNarrationNotice();
      if (!settings.narration.elevenlabs_configured) {
        $('narration-notice').textContent +=
          ' ElevenLabs is not connected; choose script-only or configure it in Settings.';
      }
    }
    if (currentPage === 'settings') {
      applyDefaults(settings.defaults, 'settings-');
      $('settings-ai-status').textContent =
        'Whisper: ' + (settings.ai.whisper_installed ? 'installed' : 'unavailable') +
        ' · Ollama: ' + (settings.ai.ollama_configured ? 'configured' : 'not configured') +
        ' · Model: ' + settings.ai.model;
      const yt = settings.youtube_import || {configured: false, status: 'not_configured'};
      $('settings-youtube-status').textContent =
        yt.status === 'ready'
          ? 'YouTube session file detected and validated. Downloads may still require browser or network verification.'
          : yt.status === 'invalid'
            ? 'Cookie file detected but invalid or unreadable. Check Netscape format and UID 10001 permissions.'
            : 'No private YouTube cookie file detected. URL downloads may require verification.';
      $('settings-voice-status').textContent =
        settings.narration.elevenlabs_configured
          ? 'ElevenLabs voiceover is configured on this server.'
          : 'ElevenLabs is not configured. AI script-only mode is available without it.';
      const platformsResponse = await fetch('/api/publishing/platforms');
      if (platformsResponse.ok) {
        const list = await platformsResponse.json();
        $('settings-platforms').replaceChildren(...list.map((p) =>
          el('p', 'connection-line', p.name + ': ' +
             (p.configured ? 'configured' : 'not connected'))
        ));
      }
    }
  } catch (err) {
    notify(err.message);
  }
}

async function savePreferences(event) {
  event.preventDefault();
  const data = {};
  for (const [key, field] of Object.entries(settingsIds)) {
    const raw = $('settings-' + field).value;
    data[key] = ['clip_seconds', 'max_clips'].includes(key) ? Number(raw) : raw;
  }
  if (!Number.isInteger(data.max_clips) || data.max_clips < 1 ||
      data.max_clips > 100) return notify('Maximum clips must be between 1 and 100.');
  try {
    const response = await fetch('/api/settings', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Could not save settings');
    $('settings-feedback').textContent = 'Preferences saved.';
  } catch (err) { notify(err.message); }
}

loadSettings();
refresh();
setInterval(() => {
  if (!['library', 'publishing', 'activity'].includes(currentPage)) return;
  // Do not recreate a video player while someone is watching it or editing a post.
  if (![...document.querySelectorAll('video')].some((v) => !v.paused) &&
      ![...document.querySelectorAll('.publish-panel')].some((p) => !p.hidden) &&
      ![...document.querySelectorAll('.clip-editor')].some((p) => p.open)) {
    refresh();
  }
}, 5000);
