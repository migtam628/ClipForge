const $ = (id) => document.getElementById(id);
let activeTab = 'url';
let polling = false;
let platforms = [];
let publishingPresets = [];
let creatorProfiles = [];
let libraryJobs = [];
const libraryExpanded = new Set();
let libraryInitialized = false;
const validPages = ['studio', 'library', 'publishing', 'posting-settings', 'analytics', 'activity', 'settings'];
const currentPage = validPages.includes(location.pathname.slice(1))
  ? location.pathname.slice(1) : 'studio';
const labels = {studio: 'Studio', library: 'Clip Library',
  publishing: 'Publishing', 'posting-settings': 'Posting settings',
  analytics: 'Analytics', activity: 'Activity', settings: 'Settings'};
$('page-crumb').textContent = labels[currentPage];
$('mobile-page-title').textContent = labels[currentPage];
document.title = 'ClipForge · ' + labels[currentPage];

// Read the running backend version so this label never goes stale between releases.
async function displayAppVersion() {
  const label = $('app-version');
  if (!label) return;
  try {
    const response = await fetch('/api/health', {cache: 'no-store'});
    if (!response.ok) throw new Error('Version check failed');
    const health = await response.json();
    if (typeof health.version !== 'string' || !health.version.trim()) {
      throw new Error('Missing app version');
    }
    label.textContent = `v${health.version} preview`;
  } catch {
    label.textContent = 'Version unavailable';
  }
}
displayAppVersion();
for (const page of validPages) {
  $('view-' + page).hidden = page !== currentPage;
}
for (const item of document.querySelectorAll('.nav-link[data-page]')) {
  const active = item.dataset.page === currentPage;
  item.classList.toggle('selected', active);
  if (active) item.setAttribute('aria-current', 'page');
}

// Mobile pages use a fixed tab bar. Secondary destinations stay in a native
// modal dialog, which traps focus and supports Escape without custom key hacks.
const mobileMoreDialog = $('mobile-more-dialog');
const mobileMoreButton = $('mobile-more-button');
const mobileMoreClose = $('mobile-more-close');
const morePages = new Set(['posting-settings', 'analytics', 'settings']);
for (const item of document.querySelectorAll('.mobile-nav-link[data-page], .mobile-more-link[data-page]')) {
  const active = item.dataset.page === currentPage;
  item.classList.toggle('selected', active);
  if (active) item.setAttribute('aria-current', 'page');
}
mobileMoreButton.classList.toggle('selected', morePages.has(currentPage));
function closeMobileMore(restoreFocus = true) {
  if (mobileMoreDialog.open) mobileMoreDialog.close();
  if (restoreFocus) mobileMoreButton.focus();
}
mobileMoreButton.addEventListener('click', () => {
  if (mobileMoreDialog.open) closeMobileMore();
  else mobileMoreDialog.showModal();
  mobileMoreButton.setAttribute('aria-expanded', String(mobileMoreDialog.open));
});
mobileMoreClose.addEventListener('click', () => closeMobileMore());
mobileMoreDialog.addEventListener('close', () => {
  mobileMoreButton.setAttribute('aria-expanded', 'false');
});
mobileMoreDialog.addEventListener('cancel', () => {
  mobileMoreButton.setAttribute('aria-expanded', 'false');
});
mobileMoreDialog.addEventListener('click', (event) => {
  if (event.target === mobileMoreDialog) closeMobileMore(false);
});
// A tablet rotated to desktop width must not retain an invisible modal overlay.
window.matchMedia('(min-width: 901px)').addEventListener('change', (event) => {
  if (event.matches && mobileMoreDialog.open) closeMobileMore(false);
});

$('url-tab').addEventListener('click', () => toggleTab('url'));
$('upload-tab').addEventListener('click', () => toggleTab('upload'));
$('create-button').addEventListener('click', createJob);
// Guided four-step creation. All options continue using the existing job API.
let journeyStep = 0;
let previewObjectUrl = null;
let previewYoutube = null;
let selectedSplitPreset = 'balanced';
const splitPresetDefinitions = {
  balanced: {name: 'Equal split', top: .5, width: .36, centers: [.22, .78]},
  'top-focus': {name: 'Top focus', top: .65, width: .36, centers: [.22, .78]},
  'bottom-focus': {name: 'Bottom focus', top: .35, width: .36, centers: [.22, .78]},
  tight: {name: 'Tight faces', top: .5, width: .28, centers: [.22, .78]},
  wide: {name: 'Wide scene', top: .5, width: .48, centers: [.26, .74]},
  swap: {name: 'Swap order', top: .5, width: .36, centers: [.78, .22]}
};
let splitBoxes = [
  {x: 0.04, y: 0.21, width: 0.36, height: 0.58},
  {x: 0.60, y: 0.21, width: 0.36, height: 0.58}
];
const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

function validateJourneySource() {
  if (activeTab === 'upload') return chosenVideoFiles().length > 0 ||
    (notify('Choose a video file to continue.'), false);
  return Boolean($('url').value.trim() || $('url-list').value.trim()) ||
    (notify('Paste a video URL to continue.'), false);
}

function setJourneyStep(step) {
  if (step > 0 && !validateJourneySource()) return;
  const previousStep = journeyStep;
  journeyStep = clamp(step, 0, 3);
  document.querySelector('.creator-panel').classList.toggle('is-framing', journeyStep === 1);
  document.querySelector('.main-content').classList.toggle('is-framing-workspace', journeyStep === 1);
  document.querySelectorAll('[data-journey-panel]').forEach((panel) => {
    panel.hidden = Number(panel.dataset.journeyPanel) !== journeyStep;
  });
  document.querySelectorAll('[data-journey]').forEach((button) => {
    const index = Number(button.dataset.journey);
    button.classList.toggle('is-active', index === journeyStep);
    button.classList.toggle('is-done', index < journeyStep);
    if (index === journeyStep) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
  $('journey-back').hidden = journeyStep === 0;
  $('journey-next').hidden = journeyStep === 3;
  $('journey-position').textContent = `Step ${journeyStep + 1} of 4`;
  if (journeyStep === 3) updateJourneyReview();
  notify('');
  if (journeyStep !== previousStep && window.matchMedia('(max-width: 900px)').matches) {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
    document.querySelector('.creator-panel').scrollIntoView({block: 'start', behavior: motion});
  }
}

function updateJourneyReview() {
  const source = activeTab === 'upload'
    ? `${chosenVideoFiles().length} local video(s)`
    : `${($('url-list').value.trim().split(/\r?\n/).filter(Boolean).length || 1)} video URL(s)`;
  const count = $('max-clips').value === 'custom'
    ? $('custom-max-clips').value : $('max-clips').value;
  const label = $('framing').selectedOptions[0]?.textContent || $('framing').value;
  const narration = $('narration').selectedOptions[0]?.textContent || $('narration').value;
  $('journey-review').replaceChildren();
  for (const [key, value] of [
    ['Source', source], ['Output', `${count} clips · ${$('clip-seconds').value}s · ${$('aspect-ratio').value}`],
    ['Framing', label], ['Captions', $('caption-style').value],
    ['Video text / voice', narration]
  ]) {
    const line = document.createElement('div');
    const strong = document.createElement('strong');
    strong.textContent = key;
    const text = document.createElement('span');
    text.textContent = value;
    line.append(strong, text);
    $('journey-review').append(line);
  }
}

document.querySelectorAll('[data-journey]').forEach((button) =>
  button.addEventListener('click', () => setJourneyStep(Number(button.dataset.journey))));
$('journey-next').addEventListener('click', () => setJourneyStep(journeyStep + 1));
$('journey-back').addEventListener('click', () => setJourneyStep(journeyStep - 1));

function splitTopRatio() { return Number($('split-ratio').value) / 100; }

function splitSourceRatio() {
  const source = $('framing-source');
  return source.videoWidth && source.videoHeight
    ? source.videoWidth / source.videoHeight : 16 / 9;
}

function splitBoxFactor(pane, box) {
  const shape = $(`split-shape-${pane}`).value;
  if (shape === 'free') return box.height / box.width;
  if (shape !== 'auto') {
    const [width, height] = shape.split(':').map(Number);
    return splitSourceRatio() * height / width;
  }
  const paneHeight = pane === 0 ? splitTopRatio() : 1 - splitTopRatio();
  return splitSourceRatio() * 1280 * paneHeight / 720;
}

function fitSplitBox(box, pane) {
  const factor = splitBoxFactor(pane, box);
  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height / 2;
  const width = Math.min(Math.max(.08, box.width), .98, .98 / factor);
  const height = width * factor;
  return {
    x: clamp(centerX - width / 2, 0, 1 - width),
    y: clamp(centerY - height / 2, 0, 1 - height), width, height
  };
}

function applySplitPreset(name) {
  const preset = splitPresetDefinitions[name];
  if (!preset) return;
  $('split-ratio').value = String(preset.top * 100);
  splitBoxes = preset.centers.map((x, i) => fitSplitBox({
    x: x - preset.width / 2, y: .5 - .1, width: preset.width, height: .2
  }, i));
  for (const i of [0, 1]) $(`split-zoom-${i}`).value = Math.round(splitBoxes[i].width * 100);
  selectedSplitPreset = name;
  renderFraming();
}

function resetSplitBoxes() { applySplitPreset('balanced'); }

function markSplitCustom() { selectedSplitPreset = null; }

function renderFraming() {
  const split = $('framing').value === 'split';
  $('split-selection').hidden = !split;
  $('split-controls').hidden = !split;
  $('framing-presets').hidden = !split;
  $('framing-workbench').classList.toggle('has-presets', split);
  $('portrait-preview').classList.toggle('is-split', split);
  const topRatio = splitTopRatio();
  document.querySelectorAll('.portrait-pane').forEach((pane, i) => {
    pane.style.flex = `${i === 0 ? topRatio : 1 - topRatio} 1 0%`;
  });
  $('split-ratio-value').textContent = `${Math.round(topRatio * 100)}% top / ${Math.round((1 - topRatio) * 100)}% bottom`;
  for (const button of document.querySelectorAll('[data-split-preset]')) {
    const chosen = button.dataset.splitPreset === selectedSplitPreset;
    button.classList.toggle('is-chosen', chosen);
    button.setAttribute('aria-pressed', String(chosen));
  }
  $('split-layout-status').textContent = `${splitPresetDefinitions[selectedSplitPreset]?.name || 'Custom layout'} · ${Math.round(topRatio * 100)}% top / ${Math.round((1 - topRatio) * 100)}% bottom`;
  document.querySelectorAll('[data-framing]').forEach((button) => {
    button.classList.toggle('is-chosen', button.dataset.framing === $('framing').value);
    button.setAttribute('aria-pressed', String(button.dataset.framing === $('framing').value));
  });
  splitBoxes.forEach((box, i) => {
    const target = document.querySelector(`[data-crop="${i}"]`);
    Object.assign(target.style, {
      left: `${box.x * 100}%`, top: `${box.y * 100}%`,
      width: `${box.width * 100}%`, height: `${box.height * 100}%`
    });
    const surface = $(i === 0 ? 'portrait-surface-a' : 'portrait-surface-b');
    if (split && (previewObjectUrl || previewYoutube)) {
      const cropAspect = splitSourceRatio() * box.width / box.height;
      const paneAspect = 720 / (1280 * (i === 0 ? topRatio : 1 - topRatio));
      const factor = cropAspect / paneAspect;
      const cover = $('split-fill').value === 'cover';
      const wider = factor > 1;
      surface.style.width = `${(cover ? wider : !wider) ? 100 * factor : 100}%`;
      surface.style.height = `${(cover ? !wider : wider) ? 100 / factor : 100}%`;
    } else {
      surface.style.width = '100%';
      surface.style.height = '100%';
    }
    for (const media of [$(i === 0 ? 'portrait-video-a' : 'portrait-video-b'),
      $(i === 0 ? 'portrait-image-a' : 'portrait-image-b')]) {
      if (split && (previewObjectUrl || previewYoutube)) {
        Object.assign(media.style, {
          width: `${100 / box.width}%`, height: `${100 / box.height}%`,
          left: `${-100 * box.x / box.width}%`, top: `${-100 * box.y / box.height}%`,
          objectFit: 'fill'
        });
      } else {
        Object.assign(media.style, {
          width: '100%', height: '100%', left: '0', top: '0',
          objectFit: $('framing').value === 'pad' ? 'contain' : 'cover'
        });
      }
    }
  });
  $('framing-instructions').textContent = (split
    ? 'Drag each colored box to position it, or drag its bottom-right handle to resize. Choose a ratio for each box; arrow keys fine-tune its position.'
    : 'Switch layouts to compare how the same source will be framed.') +
    (activeTab === 'url' && previewYoutube
      ? ' YouTube guides are approximate; review the downloaded source in the Library editor.' : '');
}

function selectedUrlForPreview() {
  const batch = $('url-list').value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return batch[0] || $('url').value.trim();
}

function clearYoutubePreview() {
  previewYoutube = null;
  $('framing-embed').removeAttribute('src');
  $('framing-embed').hidden = true;
}

function loadYoutubePreview() {
  const selected = selectedUrlForPreview();
  const parsed = parseYouTubePreview(selected);
  if (!parsed) {
    clearYoutubePreview();
    loadSourcePreview();
    $('url-preview-status').textContent = selected
      ? 'Preview supports HTTPS YouTube watch, Shorts and youtu.be links. Other URLs can still be imported.'
      : 'Enter a YouTube link first.';
    return;
  }
  previewYoutube = {...parsed, sourceUrl: selected};
  $('url-preview-status').textContent = $('url-list').value.trim()
    ? 'Preview loaded for the first URL in your batch. Continue to Framing.'
    : 'YouTube preview ready. Continue to Framing.';
  loadSourcePreview();
}

function loadSourcePreview() {
  if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
  previewObjectUrl = null;
  const files = chosenVideoFiles();
  const urlPreview = activeTab === 'url' && previewYoutube &&
    previewYoutube.sourceUrl === selectedUrlForPreview();
  if (activeTab === 'upload' && files.length === 1) {
    previewObjectUrl = URL.createObjectURL(files[0]);
  }
  const embed = $('framing-embed');
  embed.hidden = !urlPreview;
  if (urlPreview) {
    if (embed.getAttribute('src') !== previewYoutube.embedUrl) embed.src = previewYoutube.embedUrl;
  } else embed.removeAttribute('src');
  for (const id of ['framing-source', 'portrait-video-a', 'portrait-video-b']) {
    const video = $(id);
    video.pause();
    video.hidden = !previewObjectUrl;
    video.removeAttribute('src');
    if (previewObjectUrl) {
      video.src = previewObjectUrl;
      video.load();
    }
  }
  for (const suffix of ['a', 'b']) {
    const img = $(`portrait-image-${suffix}`);
    const placeholder = img.closest('.portrait-pane').querySelector('.portrait-placeholder');
    img.hidden = true;
    img.removeAttribute('src');
    placeholder.hidden = Boolean(previewObjectUrl);
    if (urlPreview) {
      const expectedId = previewYoutube.id;
      img.onload = () => {
        if (previewYoutube?.id !== expectedId || activeTab !== 'url') return;
        img.hidden = false;
        placeholder.hidden = true;
      };
      img.onerror = () => { img.hidden = true; placeholder.hidden = false; };
      img.src = previewYoutube.thumbnailUrl;
    }
  }
  $('framing-empty').hidden = Boolean(previewObjectUrl || urlPreview);
  $('framing-source-label').textContent = previewObjectUrl ? files[0].name : urlPreview
    ? 'YouTube player · first URL in batch if provided'
    : 'Select a file or load a supported YouTube link';
  $('portrait-preview-note').textContent = urlPreview
    ? 'Still YouTube thumbnail crop guide only, not a synchronized video preview. Final framing uses downloaded footage; verify it in the Library timeline.'
    : previewObjectUrl
      ? 'Use the original video controls to play, pause or scrub. Both vertical panes follow the same time. FFmpeg renders the selected ratio and boxes on export.'
      : 'Choose a local video for synchronized playback. Final exports use full-resolution FFmpeg rendering.';
  $('framing-stage').style.aspectRatio = '16 / 9';
  renderFraming();
}

$('load-url-preview').addEventListener('click', loadYoutubePreview);
for (const id of ['url', 'url-list']) {
  $(id).addEventListener('input', () => {
    if (previewYoutube && previewYoutube.sourceUrl !== selectedUrlForPreview()) {
      clearYoutubePreview();
      $('url-preview-status').textContent = 'Source changed. Load the preview again for this URL.';
      if (activeTab === 'url') loadSourcePreview();
    }
  });
}

$('framing-source').addEventListener('loadedmetadata', () => {
  const source = $('framing-source');
  if (source.videoWidth && source.videoHeight) {
    $('framing-stage').style.aspectRatio = `${source.videoWidth} / ${source.videoHeight}`;
    splitBoxes = splitBoxes.map((box, i) => fitSplitBox(box, i));
    for (const i of [0, 1]) $(`split-zoom-${i}`).value = Math.round(splitBoxes[i].width * 100);
    renderFraming();
  }
});

// One transport for all three local preview videos. The source player owns
// play/pause and scrubbing, while silent portrait views follow its timecode.
const framingMaster = $('framing-source');
const framingFollowers = [$('portrait-video-a'), $('portrait-video-b')];
function syncFramingFollowers(force = false) {
  if (!previewObjectUrl) return;
  for (const follower of framingFollowers) {
    if (follower.readyState < HTMLMediaElement.HAVE_METADATA) continue;
    if (force || Math.abs(follower.currentTime - framingMaster.currentTime) > .16) {
      follower.currentTime = framingMaster.currentTime;
    }
    follower.playbackRate = framingMaster.playbackRate;
  }
}
framingMaster.addEventListener('play', () => {
  syncFramingFollowers(true);
  for (const follower of framingFollowers) follower.play().catch(() => {});
});
framingMaster.addEventListener('pause', () => {
  for (const follower of framingFollowers) follower.pause();
  syncFramingFollowers(true);
});
framingMaster.addEventListener('seeking', () => syncFramingFollowers(true));
framingMaster.addEventListener('timeupdate', () => syncFramingFollowers());
framingMaster.addEventListener('ratechange', () => syncFramingFollowers());
for (const follower of framingFollowers) {
  follower.addEventListener('loadedmetadata', () => {
    syncFramingFollowers(true);
    if (!framingMaster.paused) follower.play().catch(() => {});
  });
}

document.querySelectorAll('[data-framing]').forEach((button) =>
  button.addEventListener('click', () => { $('framing').value = button.dataset.framing;
    if (button.dataset.framing === 'split') $('aspect-ratio').value = '9:16';
    renderFraming();
  }));
$('framing').addEventListener('change', () => {
  if ($('framing').value === 'split') $('aspect-ratio').value = '9:16';
  renderFraming();
});
$('aspect-ratio').addEventListener('change', () => {
  if ($('framing').value === 'split' && $('aspect-ratio').value !== '9:16') {
    $('framing').value = 'smart';
    notify('Split in two is available for 9:16 clips; switched framing to Auto focus.');
  }
  renderFraming();
});
$('split-reset').addEventListener('click', resetSplitBoxes);
for (const button of document.querySelectorAll('[data-split-preset]')) {
  button.addEventListener('click', () => applySplitPreset(button.dataset.splitPreset));
}
$('split-ratio').addEventListener('input', () => {
  splitBoxes = splitBoxes.map((box, i) => fitSplitBox(box, i));
  for (const i of [0, 1]) $(`split-zoom-${i}`).value = Math.round(splitBoxes[i].width * 100);
  markSplitCustom();
  renderFraming();
});
$('split-fill').addEventListener('change', renderFraming);
for (const i of [0, 1]) {
  $(`split-shape-${i}`).addEventListener('change', () => {
    splitBoxes[i] = fitSplitBox(splitBoxes[i], i);
    $(`split-zoom-${i}`).value = Math.round(splitBoxes[i].width * 100);
    markSplitCustom();
    renderFraming();
  });
}

for (const i of [0, 1]) {
  const button = document.querySelector(`[data-crop="${i}"]`);
  let point = null;
  button.addEventListener('pointerdown', (event) => {
    if ($('framing').value !== 'split') return;
    point = {x: event.clientX, y: event.clientY,
      resizing: Boolean(event.target.closest('.crop-resize-handle'))};
    button.setPointerCapture(event.pointerId);
  });
  button.addEventListener('pointermove', (event) => {
    if (!point) return;
    const stage = $('framing-stage').getBoundingClientRect();
    const box = splitBoxes[i];
    const dx = (event.clientX - point.x) / stage.width;
    const dy = (event.clientY - point.y) / stage.height;
    if (point.resizing) {
      if ($(`split-shape-${i}`).value === 'free') {
        box.width = clamp(box.width + dx, .08, 1 - box.x);
        box.height = clamp(box.height + dy, .08, 1 - box.y);
      } else {
        const factor = splitBoxFactor(i, box);
        const widthDelta = Math.abs(dx) >= Math.abs(dy / factor) ? dx : dy / factor;
        box.width = clamp(box.width + widthDelta, .08, Math.min(1 - box.x, (1 - box.y) / factor));
        box.height = box.width * factor;
      }
      $(`split-zoom-${i}`).value = Math.round(box.width * 100);
    } else {
      box.x = clamp(box.x + dx, 0, 1 - box.width);
      box.y = clamp(box.y + dy, 0, 1 - box.height);
    }
    point = {...point, x: event.clientX, y: event.clientY};
    markSplitCustom();
    renderFraming();
  });
  const stop = () => { point = null; };
  button.addEventListener('pointerup', stop);
  button.addEventListener('pointercancel', stop);
  button.addEventListener('lostpointercapture', stop);
  button.addEventListener('keydown', (event) => {
    const delta = {ArrowLeft: [-.02, 0], ArrowRight: [.02, 0],
      ArrowUp: [0, -.02], ArrowDown: [0, .02]}[event.key];
    if (!delta) return;
    event.preventDefault();
    splitBoxes[i].x = clamp(splitBoxes[i].x + delta[0], 0, 1 - splitBoxes[i].width);
    splitBoxes[i].y = clamp(splitBoxes[i].y + delta[1], 0, 1 - splitBoxes[i].height);
    markSplitCustom();
    renderFraming();
  });
  $(`split-zoom-${i}`).addEventListener('input', (event) => {
    const box = splitBoxes[i];
    const w = Number(event.target.value) / 100;
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    const scaledHeight = box.height * w / box.width;
    const updated = fitSplitBox({x: cx - w / 2, y: cy - scaledHeight / 2,
      width: w, height: scaledHeight}, i);
    Object.assign(box, updated);
    markSplitCustom();
    renderFraming();
  });
}

$('caption-style').addEventListener('change', () => {
  document.querySelectorAll('[data-caption]').forEach((button) => {
    button.classList.toggle('is-chosen', button.dataset.caption === $('caption-style').value);
    button.setAttribute('aria-pressed', String(button.dataset.caption === $('caption-style').value));
  });
});
document.querySelectorAll('[data-caption]').forEach((button) =>
  button.addEventListener('click', () => {
    $('caption-style').value = button.dataset.caption;
    $('caption-style').dispatchEvent(new Event('change'));
  }));
$('caption-style').dispatchEvent(new Event('change'));
applySplitPreset('balanced');
$('save-creator-profile').addEventListener('click', saveCreatorProfile);
$('creator-profile-select').addEventListener('change', applyCreatorProfile);
for (const button of document.querySelectorAll('[data-refresh]')) {
  button.addEventListener('click', refresh);
}
// Library search and filters are local: no new server requests for each keystroke.
for (const id of ['library-search', 'library-filter', 'library-sort']) {
  $(id).addEventListener(id === 'library-search' ? 'input' : 'change', renderLibrary);
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
  loadSourcePreview();
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
    note.textContent = 'For story-like videos: a scene-by-scene spoken voiceover. ElevenLabs may charge per section; original audio is lowered under the narrator.';
  } else if (mode === 'context') {
    note.textContent = 'Adds short on-screen context tied to each video scene. Keeps original audio untouched. Unlike subtitles, these labels describe the moment instead of repeating dialogue.';
  } else if (mode === 'script') {
    note.textContent = 'Saves a timestamped text script for review or copying. It is not burned into the video and no voice is generated.';
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
  loadSourcePreview();
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
    split_boxes: splitBoxes.map((box) => ({...box})),
    split_ratio: splitTopRatio(),
    split_fill: $('split-fill').value,
    hook_style: $('hook-style').value,
    narration: $('narration').value,
    narration_style: $('narration-style').value,
    narration_language: $('narration-language').value,
    narration_basis: $('narration-basis').value,
    content_niche: $('content-niche').value,
    clip_focus: $('clip-focus').value.trim(),
    use_voice_markers: $('use-voice-markers').checked,
    brand_text: $('brand-text').value.trim(),
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
    for (const [k, v] of Object.entries(options)) data.set(k,
      typeof v === 'object' ? JSON.stringify(v) : String(v));
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
  editor.append(el('summary', '', '✦ Open timeline editor'));
  const form = el('form', 'clip-editor-form');
  const fields = el('div', 'clip-edit-fields');
  const workspaceBar = el('div', 'timeline-workspace-bar');
  workspaceBar.append(el('strong', '', 'ClipForge Timeline'));
  const focusButton = el('button', 'small-action', '⛶ Expand editor');
  focusButton.type = 'button';
  focusButton.addEventListener('click', () => {
    editor.classList.toggle('timeline-expanded');
    editor.open = true;
    focusButton.textContent = editor.classList.contains('timeline-expanded')
      ? '↙ Exit full screen' : '⛶ Expand editor';
  });
  editor.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && editor.classList.contains('timeline-expanded')) {
      editor.classList.remove('timeline-expanded');
      focusButton.textContent = '⛶ Expand editor';
      event.stopPropagation();
    }
  });
  workspaceBar.append(focusButton);
  form.append(workspaceBar);

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

  const timeline = el('section', 'timeline-workspace');
  timeline.setAttribute('aria-label', 'Source video timeline');
  const monitor = el('div', 'timeline-monitor');
  const sourceVideo = el('video', 'timeline-source-monitor');
  sourceVideo.controls = true;
  sourceVideo.preload = 'metadata';
  sourceVideo.src = `/api/jobs/${job.id}/source`;
  const monitorHelp = el('p', 'helper-note',
    'Source monitor · scrubbing previews the original footage before a new render.');
  sourceVideo.addEventListener('error', () => {
    monitorHelp.textContent = 'Original source preview is unavailable in this browser. ' +
      'Keep using the numerical trim controls; the source file remains on the server.';
  });
  monitor.append(sourceVideo, monitorHelp);
  const tracks = el('div', 'timeline-tracks');
  const time = (seconds) => {
    const value = Math.max(0, Number(seconds) || 0);
    return String(Math.floor(value / 60)).padStart(2, '0') + ':' +
      (value % 60).toFixed(1).padStart(4, '0');
  };
  const timeCode = el('div', 'timeline-timecode');
  const currentTime = el('strong', '', time(clip.start));
  const selectionTime = el('span', '', 'IN ' + time(clip.start) +
    ' · OUT ' + time(clip.start + clip.duration));
  timeCode.append(currentTime, selectionTime);
  const ruler = el('input', 'timeline-scrubber');
  ruler.type = 'range';
  ruler.min = '0';
  ruler.max = String(job.duration || (clip.start + clip.duration));
  ruler.step = '0.1';
  ruler.value = String(clip.start);
  ruler.setAttribute('aria-label', 'Scrub original video');
  ruler.addEventListener('input', () => {
    sourceVideo.currentTime = Number(ruler.value);
    currentTime.textContent = time(ruler.value);
  });
  sourceVideo.addEventListener('timeupdate', () => {
    ruler.value = String(sourceVideo.currentTime);
    currentTime.textContent = time(sourceVideo.currentTime);
  });
  sourceVideo.addEventListener('loadedmetadata', () => {
    sourceVideo.currentTime = Math.min(Number(clip.start), sourceVideo.duration || 0);
  }, {once: true});
  tracks.append(timeCode, ruler);

  const totalSeconds = Math.max(3, Number(job.duration) || clip.start + clip.duration);
  const clipRail = el('div', 'timeline-lane-rail');
  const selection = el('div', 'timeline-video-selection');
  function repaintSelection() {
    const begin = Number(start.value);
    const end = begin + Number(duration.value);
    selection.style.left = (100 * begin / totalSeconds) + '%';
    selection.style.width = (100 * (end - begin) / totalSeconds) + '%';
    selection.textContent = time(begin) + ' – ' + time(end);
    selectionTime.textContent = 'IN ' + time(begin) + ' · OUT ' + time(end);
  }
  clipRail.append(selection);
  const trim = el('div', 'timeline-trim-controls');
  const inHandle = el('input');
  const outHandle = el('input');
  for (const [handle, label] of [[inHandle, 'Trim in'], [outHandle, 'Trim out']]) {
    handle.type = 'range';
    handle.min = '0';
    handle.max = String(totalSeconds);
    handle.step = '0.1';
    handle.setAttribute('aria-label', label);
  }
  inHandle.value = start.value;
  outHandle.value = String(Number(start.value) + Number(duration.value));
  inHandle.addEventListener('input', () => {
    const end = Number(outHandle.value);
    start.value = String(Math.min(Number(inHandle.value), end - 3).toFixed(1));
    duration.value = String((end - Number(start.value)).toFixed(1));
    inHandle.value = start.value;
    repaintSelection();
    sourceVideo.currentTime = Number(start.value);
  });
  outHandle.addEventListener('input', () => {
    const end = Math.max(Number(outHandle.value), Number(start.value) + 3);
    outHandle.value = String(end.toFixed(1));
    duration.value = String((end - Number(start.value)).toFixed(1));
    repaintSelection();
    sourceVideo.currentTime = Math.max(Number(start.value), end - 0.2);
  });
  for (const field of [start, duration]) field.addEventListener('input', () => {
    inHandle.value = start.value;
    outHandle.value = String(Number(start.value) + Number(duration.value));
    repaintSelection();
  });
  trim.append(inHandle, outHandle);
  const videoLane = el('div', 'timeline-lane');
  videoLane.append(el('span', 'timeline-lane-label', 'V1 · Original video'), clipRail, trim);
  tracks.append(videoLane);
  repaintSelection();
  // Cut-list editor: each item is a source-time range, ordered on the output
  // timeline. The original file is never changed.
  let cutsActive = Array.isArray(clip.segments) && clip.segments.length > 0;
  let cuts = cutsActive ? clip.segments.map(s => ({start: Number(s.start), duration: Number(s.duration)}))
    : [{start: Number(clip.start), duration: Number(clip.duration)}];
  let selectedCut = 0;
  let previewingCuts = false;
  let previewCutIndex = 0;
  const cutPanel = el('div', 'timeline-cut-panel');
  const cutControls = el('div', 'timeline-cut-actions');
  const sequenceRail = el('div', 'timeline-sequence-rail');
  const cutList = el('div', 'timeline-cut-list');
  const cutNotice = el('p', 'helper-note',
    'Drag numbered blocks to reorder, or use the arrow buttons.');
  const cutLabel = el('strong', '', 'V2 · Cut & reorder');
  function cutButton(text, action) {
    const button = el('button', 'small-action', text);
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
  }
  const drawCuts = () => {
    sequenceRail.replaceChildren();
    cutList.replaceChildren();
    if (!cutsActive) {
      cutNotice.textContent = 'Original continuous clip. Split at the playhead to enable editing.';
      return;
    }
    const total = cuts.reduce((sum, part) => sum + part.duration, 0);
    cutNotice.textContent = `Output length ${time(total)} · ${cuts.length} ordered segments. ` +
      'Drag blocks or use arrows; narration timing must be regenerated after changing cuts.';
    for (const [index, part] of cuts.entries()) {
      const block = el('button', 'timeline-sequence-block', String(index + 1));
      block.type = 'button';
      block.title = `${time(part.start)} → ${time(part.start + part.duration)}`;
      block.style.flex = String(Math.max(0.5, part.duration));
      block.classList.toggle('active', index === selectedCut);
      block.draggable = true;
      block.addEventListener('click', () => {
        selectedCut = index;
        sourceVideo.currentTime = part.start;
        drawCuts();
      });
      block.addEventListener('dragstart', event => {
        event.dataTransfer?.setData('text/plain', String(index));
      });
      block.addEventListener('dragover', event => event.preventDefault());
      block.addEventListener('drop', event => {
        event.preventDefault();
        const from = Number(event.dataTransfer?.getData('text/plain'));
        if (!Number.isInteger(from) || from < 0 || from >= cuts.length || from === index) return;
        const [moving] = cuts.splice(from, 1);
        const target = index;
        cuts.splice(target, 0, moving);
        selectedCut = target;
        drawCuts();
      });
      sequenceRail.append(block);
      const row = el('div', 'timeline-cut-row');
      const jump = cutButton(`${index + 1} · ${time(part.start)} + ${time(part.duration)}`, () => {
        selectedCut = index;
        previewingCuts = false;
        sourceVideo.pause();
        sourceVideo.currentTime = part.start;
        drawCuts();
      });
      jump.classList.toggle('active', selectedCut === index);
      jump.style.flex = String(Math.max(1, part.duration));
      row.append(jump);
      row.append(cutButton('↑', () => {
        if (index === 0) return;
        [cuts[index - 1], cuts[index]] = [cuts[index], cuts[index - 1]];
        selectedCut = index - 1;
        drawCuts();
      }));
      row.append(cutButton('↓', () => {
        if (index === cuts.length - 1) return;
        [cuts[index + 1], cuts[index]] = [cuts[index], cuts[index + 1]];
        selectedCut = index + 1;
        drawCuts();
      }));
      row.append(cutButton('×', () => {
        if (cuts.length === 1) return notify('Keep at least one video segment.');
        cuts.splice(index, 1);
        selectedCut = Math.min(index, cuts.length - 1);
        drawCuts();
      }));
      cutList.append(row);
    }
  };
  cutControls.append(
    cutButton('✂ Split at playhead', () => {
      const at = Number(sourceVideo.currentTime);
      const index = cutsActive && cuts[selectedCut] &&
        at > cuts[selectedCut].start + 0.5 &&
        at < cuts[selectedCut].start + cuts[selectedCut].duration - 0.5
        ? selectedCut : cuts.findIndex(part =>
          at > part.start + 0.5 && at < part.start + part.duration - 0.5);
      if (index < 0) return notify('Position the playhead at least 0.5s inside a segment.');
      if (cuts.length >= 12) return notify('The timeline is limited to 12 segments.');
      const original = cuts[index];
      const first = {start: original.start, duration: Number((at - original.start).toFixed(2))};
      const second = {start: Number(at.toFixed(2)),
        duration: Number((original.start + original.duration - at).toFixed(2))};
      cuts.splice(index, 1, first, second);
      cutsActive = true;
      selectedCut = index + 1;
      drawCuts();
    }),
    cutButton('+ Add selected range', () => {
      if (cuts.length >= 12) return notify('The timeline is limited to 12 segments.');
      const begin = Number(start.value), length = Number(duration.value);
      if (!Number.isFinite(begin) || !Number.isFinite(length) || length < 0.5 ||
          begin < 0 || begin + length > totalSeconds + 0.05)
        return notify('Choose a valid source in/out range first.');
      cuts.push({start: begin, duration: length});
      cutsActive = true;
      selectedCut = cuts.length - 1;
      drawCuts();
    }),
    cutButton('▶ Preview cut order', () => {
      if (!cutsActive) return notify('Split the clip or add a source range first.');
      previewCutIndex = 0;
      previewingCuts = true;
      sourceVideo.currentTime = cuts[0].start;
      sourceVideo.play().catch(() => { previewingCuts = false; });
    }),
    cutButton('↺ Reset cuts', () => {
      previewingCuts = false;
      sourceVideo.pause();
      cutsActive = false;
      cuts = [{start: Number(clip.start), duration: Number(clip.duration)}];
      selectedCut = 0;
      drawCuts();
    })
  );
  sourceVideo.addEventListener('timeupdate', () => {
    if (!previewingCuts) return;
    const part = cuts[previewCutIndex];
    if (!part || sourceVideo.currentTime < part.start + part.duration - 0.08) return;
    previewCutIndex++;
    if (previewCutIndex >= cuts.length) {
      previewingCuts = false;
      sourceVideo.pause();
    } else {
      sourceVideo.currentTime = cuts[previewCutIndex].start;
    }
  });
  ruler.addEventListener('input', () => { previewingCuts = false; });
  cutPanel.append(cutLabel, cutControls, sequenceRail, cutList, cutNotice);
  drawCuts();
  timeline.append(monitor, tracks);
  form.append(timeline, cutPanel);

  const effect = dropdown('Color treatment', [
    ['none', 'Natural'], ['pop', 'Vibrant pop'],
    ['cinema', 'Cinematic'], ['noir', 'Black & white']
  ], clip.effect || job.options.effect);

  const outputRatio = dropdown('Export aspect ratio', [
    ['9:16', '9:16 · Vertical shorts'], ['4:5', '4:5 · Portrait'],
    ['3:4', '3:4 · Portrait'], ['2:3', '2:3 · Portrait'],
    ['1:1', '1:1 · Square'], ['4:3', '4:3 · Classic'],
    ['3:2', '3:2 · Landscape'], ['16:9', '16:9 · Widescreen'],
    ['21:9', '21:9 · Cinematic']
  ], clip.aspect_ratio || job.options.aspect_ratio);

  const framing = dropdown('Framing', [
    ['smart', 'Smart · follow faces'], ['blur', 'Blurred background'],
    ['crop', 'Centered crop'], ['pad', 'Keep full frame'],
    ['split', 'Two-person reaction split · 9:16 only']
  ], clip.framing || job.options.framing);
  const splitFill = dropdown('Split panel fit', [
    ['cover', 'Fill panel · trim edges'], ['contain', 'Show full crop · add bars']
  ], clip.split_fill || job.options.split_fill || 'cover');

  const captions = dropdown('Captions', [
    ['karaoke', 'Word-aligned karaoke'], ['pop', 'Animated pop words'],
    ['bold', 'Bold yellow'], ['classic', 'Classic white'],
    ['neon', 'Neon pink'], ['none', 'No captions']
  ], clip.caption_style || (clip.captions_added ? job.options.caption_style : 'none'));

  const hook = dropdown('Opening headline', [
    ['headline', 'Show first 3 seconds'], ['none', 'No headline']
  ], clip.hook_style || 'none');

  const brandText = el('input');
  brandText.maxLength = 36;
  brandText.value = clip.brand_text || '';
  brandText.placeholder = 'Optional channel text sticker';
  control('Brand / sticker text', brandText);

  const script = el('textarea');
  script.rows = 3;
  script.maxLength = 1200;
  script.value = clip.script || '';
  script.placeholder = 'Optional story script';
  if (!clip.storyboard?.length) control('Story script (optional)', script);

  let beatsChanged = false;
  const beatEditors = [];
  if (clip.storyboard?.length) {
    const beatList = el('div', 'timeline-beat-list');
    beatList.append(el('strong', '', clip.context_added
      ? 'On-screen context · review every label before publishing'
      : 'Timed scene script · review every line before publishing'));
    const beatRail = el('div', 'timeline-lane-rail timeline-beat-rail');
    for (const [index, beat] of clip.storyboard.entries()) {
      const row = el('label', 'timeline-beat-edit');
      const count = clip.context_added
        ? Math.max(3, Math.min(12, Math.floor((Number(beat.end) - Number(beat.start)) * 1.5)))
        : Math.max(3, Math.floor((Number(beat.end) - Number(beat.start)) * 2.4));
      row.append(el('span', '', `Scene ${index + 1} · ${time(beat.start)}–${time(beat.end)} · about ${count} words maximum`));
      const text = el('textarea');
      text.rows = 2;
      text.maxLength = 350;
      text.value = beat.text || '';
      text.addEventListener('input', () => { beatsChanged = true; });
      row.append(text);
      beatEditors.push({start: beat.start, end: beat.end, text});
      beatList.append(row);
      const segment = el('span', 'timeline-beat-segment', String(index + 1));
      segment.style.left = (100 * (cutsActive ? Number(beat.start) / Number(clip.duration)
        : (Number(clip.start) + Number(beat.start)) / totalSeconds)) + '%';
      segment.style.width = (100 * (Number(beat.end) - Number(beat.start)) /
        (cutsActive ? Number(clip.duration) : totalSeconds)) + '%';
      segment.title = `Scene text ${index + 1}`;
      segment.tabIndex = 0;
      segment.setAttribute('role', 'button');
      const seekScene = () => {
        let seekTo = Number(clip.start) + Number(beat.start);
        if (cutsActive) {
          let remaining = Number(beat.start);
          for (const part of cuts) {
            if (remaining <= part.duration) {
              seekTo = part.start + remaining;
              break;
            }
            remaining -= part.duration;
          }
        }
        sourceVideo.currentTime = seekTo;
        text.focus();
      };
      segment.addEventListener('click', seekScene);
      segment.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          seekScene();
        }
      });
      beatRail.append(segment);
    }
    const voiceLane = el('div', 'timeline-lane');
    voiceLane.append(el('span', 'timeline-lane-label', 'T1 · Timed scene text'), beatRail);
    tracks.append(voiceLane);
    form.append(beatList);
  }

  const audioLane = el('div', 'timeline-lane');
  const audioRail = el('div', 'timeline-lane-rail timeline-audio-rail',
    'Original source audio · editable level');
  audioLane.append(el('span', 'timeline-lane-label', 'A1 · Source audio'), audioRail);
  tracks.append(audioLane);
  const audioMixer = el('div', 'timeline-audio-mixer');
  function volumeSlider(labelText, initial) {
    const wrapper = el('label', 'timeline-volume-control');
    const label = el('span', '', labelText);
    const output = el('output', '', Math.round(initial * 100) + '%');
    const slider = el('input');
    slider.type = 'range'; slider.min = '0'; slider.max = '200';
    slider.step = '5'; slider.value = String(Math.round(initial * 100));
    slider.setAttribute('aria-label', labelText);
    slider.addEventListener('input', () => { output.textContent = slider.value + '%'; });
    wrapper.append(label, slider, output);
    audioMixer.append(wrapper);
    return slider;
  }
  const sourceVolume = volumeSlider('Original audio',
    Number(clip.source_volume ?? (clip.voiceover_added ? 0.18 : 1)));
  const voiceVolume = volumeSlider('Narrator voice', Number(clip.voice_volume ?? 1));
  const musicVolume = volumeSlider('Music track', Number(clip.music_volume ?? 0.25));
  form.append(audioMixer);
  const musicPanel = el('div', 'timeline-music-panel');
  const musicLane = el('div', 'timeline-lane');
  musicLane.append(el('span', 'timeline-lane-label', 'A2 · Background music'),
    el('div', 'timeline-lane-rail timeline-music-rail', 'Optional uploaded music · loops to clip length'));
  tracks.append(musicLane);
  const musicToggle = el('label', 'clip-edit-consent');
  const musicEnabled = el('input');
  musicEnabled.type = 'checkbox';
  let musicReady = Boolean(job.music_file);
  musicEnabled.checked = Boolean(clip.music_enabled && musicReady);
  musicToggle.append(musicEnabled, el('span', '', 'Mix background music into this export'));
  const musicRightsRow = el('label', 'clip-edit-consent');
  const musicRights = el('input');
  musicRights.type = 'checkbox';
  musicRightsRow.append(musicRights, el('span', '',
    'I own or have permission to use the uploaded music in my clips'));
  const musicUpload = el('input', 'timeline-music-input');
  musicUpload.type = 'file';
  musicUpload.accept = '.mp3,.m4a,.wav,.ogg,audio/*';
  musicUpload.setAttribute('aria-label', 'Upload music track');
  const musicStatus = el('span', 'helper-note', musicReady
    ? 'Music track available on this job' : 'No music uploaded');
  musicUpload.addEventListener('change', async () => {
    const file = musicUpload.files?.[0];
    if (!file) return;
    if (!musicRights.checked) {
      musicUpload.value = '';
      return notify('Confirm music usage rights before uploading.');
    }
    if (file.size > 50 * 1024 * 1024) return notify('Music must be 50 MB or smaller.');
    const data = new FormData();
    data.append('file', file);
    data.append('rights_confirmed', 'true');
    musicUpload.disabled = true;
    musicStatus.textContent = 'Uploading music…';
    try {
      const response = await fetch(`/api/jobs/${job.id}/music`, {method: 'POST', body: data});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string'
        ? result.detail : 'Could not upload music');
      musicReady = true;
      musicEnabled.checked = true;
      musicStatus.textContent = 'Music ready · ' + file.name;
      notify('Music saved for this job. It will be mixed when you render.');
    } catch (error) {
      musicStatus.textContent = 'Music upload failed';
      notify(error.message);
    } finally {
      musicUpload.disabled = false;
    }
  });
  musicPanel.append(musicToggle, musicRightsRow, musicUpload, musicStatus);
  form.append(musicPanel);

  const regenerate = el('label', 'clip-edit-consent');
  const regenerateCheck = el('input');
  regenerateCheck.type = 'checkbox';
  regenerate.append(regenerateCheck, el('span', '',
    'Regenerate scene-aware text based on this video segment (replaces manual text)'));
  const basis = dropdown('Text evidence', [
    ['video', 'Video frames + speech (requires local vision model)'],
    ['speech', 'Transcript only (no visual analysis)']
  ], 'video');

  const contextRow = el('label', 'clip-edit-consent');
  const context = el('input');
  context.type = 'checkbox';
  context.checked = Boolean(clip.context_added);
  contextRow.append(context, el('span', '',
    'Show short contextual text on screen (above speech captions; no voiceover)'));

  const voiceRow = el('label', 'clip-edit-consent');
  const voice = el('input');
  voice.type = 'checkbox';
  voiceRow.append(voice, el('span', '',
    'Generate new spoken commentary with ElevenLabs (external API, may incur a charge)'));
  voice.addEventListener('change', () => {
    if (voice.checked && Number(sourceVolume.value) === 100) {
      sourceVolume.value = '20';
      sourceVolume.dispatchEvent(new Event('input'));
    }
  });
  const approvalRow = el('label', 'clip-edit-consent');
  const approval = el('input');
  approval.type = 'checkbox';
  approvalRow.append(approval, el('span', '',
    'I approve the external text-to-speech request and possible usage charge.'));
  approvalRow.hidden = true;
  voice.addEventListener('change', () => { approvalRow.hidden = !voice.checked; });

  const explain = el('p', 'helper-note',
    'Edits use the original source and create a new version. Contextual text is visual only and keeps your original audio. Story voiceover costs require approval. Trim changes need new scene timing.');
  const actions = el('div', 'clip-edit-actions');
  const save = el('button', 'small-action primary-action', 'Render revised clip');
  save.type = 'submit';
  if (['queued', 'rendering'].includes(clip.revision_state)) save.disabled = true;
  actions.append(save);
  form.append(fields, contextRow, regenerate, voiceRow, approvalRow, explain, actions);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const begin = Number(start.value);
    const span = Number(duration.value);
    const cutDuration = cuts.reduce((sum, item) => sum + item.duration, 0);
    const orderedCuts = cutsActive ? cuts.map(item => ({
      start: item.start, duration: item.duration
    })) : [];
    const priorCuts = Array.isArray(clip.segments) ? clip.segments : [];
    const cutsChanged = cutsActive && (orderedCuts.length !== priorCuts.length ||
      orderedCuts.some((part, index) =>
        Math.abs(part.start - Number(priorCuts[index]?.start)) > 0.05 ||
        Math.abs(part.duration - Number(priorCuts[index]?.duration)) > 0.05));
    if (!cutsActive && (!Number.isFinite(begin) || !Number.isFinite(span) ||
        span < 3 || span > 120 || begin < 0 ||
        begin + span > Number(job.duration || 0) + 0.05))
      return notify('Trim needs to fit within the original video (3–120 seconds).');
    if (cutsActive && (cutDuration < 3 || cutDuration > 120 ||
        cuts.some(part => part.duration < 0.5 || part.start < 0 ||
          part.start + part.duration > Number(job.duration || 0) + 0.05)))
      return notify('Combined cuts must last 3–120 seconds and stay within the source.');
    if (cutsChanged && beatsChanged && !regenerateCheck.checked)
      return notify('Rearranged cuts need fresh scene timing. Select Regenerate scene text.');
    if (cutsChanged && voice.checked && !regenerateCheck.checked)
      return notify('Regenerate timed scene text before voicing a rearranged video.');
    if (voice.checked && context.checked)
      return notify('Choose contextual on-screen text or a spoken story voiceover for this export.');
    if (context.checked && !regenerateCheck.checked && !beatEditors.length)
      return notify('Regenerate scene-aware text first, then render contextual overlays.');
    if (context.checked && !regenerateCheck.checked &&
        (cutsChanged || cutsActive || Math.abs(begin - Number(clip.start)) > 0.05 ||
         Math.abs(span - Number(clip.duration)) > 0.05))
      return notify('Revised timing needs regenerated contextual text.');
    if (musicEnabled.checked && !musicReady)
      return notify('Upload a music track before enabling background music.');
    if (framing.value === 'split' && outputRatio.value !== '9:16')
      return notify('Choose 9:16 to render the two-person split, or change the framing mode.');
    if (voice.checked && (!approval.checked ||
        (!script.value.trim() && !regenerateCheck.checked && !beatEditors.some(b => b.text.value.trim()))))
      return notify('Approve ElevenLabs usage and enter a script or enable video analysis.');
    const useBeatOverrides = !cutsChanged && beatEditors.length && !regenerateCheck.checked &&
      (beatsChanged || voice.checked || context.checked);
    if (beatsChanged && regenerateCheck.checked && !cutsActive)
      return notify('Choose your manually edited scene text or AI regeneration, not both.');
    if (useBeatOverrides && !cutsActive && (Math.abs(begin - Number(clip.start)) > 0.05 ||
        Math.abs(span - Number(clip.duration)) > 0.05))
      return notify('Scene text is tied to the old timing. Keep the original trim or regenerate scene text.');
    const settings = {
      start: cutsActive ? 0 : begin, duration: cutsActive ? Number(cutDuration.toFixed(2)) : span,
      segments: orderedCuts, title: title.value.trim(),
      effect: effect.value, framing: framing.value, aspect_ratio: outputRatio.value,
      split_boxes: clip.split_boxes?.length === 2 ? clip.split_boxes :
        job.options.split_boxes,
      split_ratio: clip.split_ratio ?? job.options.split_ratio ?? 0.5,
      split_fill: splitFill.value,
      caption_style: captions.value, hook_style: hook.value,
      script: cutsActive ? '' : script.value.trim(), voiceover: voice.checked,
      manual_beats: useBeatOverrides ? beatEditors.map(b => ({
        start: b.start, end: b.end, text: b.text.value.trim()
      })) : [],
      source_volume: Number(sourceVolume.value) / 100,
      voice_volume: Number(voiceVolume.value) / 100,
      music_enabled: musicEnabled.checked,
      music_volume: Number(musicVolume.value) / 100,
      brand_text: brandText.value.trim(),
      regenerate_storyboard: regenerateCheck.checked,
      context_overlay: context.checked,
      narration_basis: basis.value,
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
  const wrapper = el('article', mode === 'library' ? 'job library-job' : 'job');
  const head = el('div', 'job-head');
  const meta = el('div');
  const progress = Number.isFinite(Number(job.progress))
    ? Math.min(100, Math.max(0, Math.round(Number(job.progress)))) : 0;
  const inProgress = !['completed', 'failed'].includes(job.status);
  meta.append(el('h3', '', job.label), el('p', '', `${new Date(job.created_at).toLocaleString()} · ${job.options.aspect_ratio} · ${job.options.clip_seconds}s · ${job.options.selection_mode === 'ai' ? 'AI selected' : 'Audio-energy selected'}${job.resume_count ? ' · resumed ' + job.resume_count + 'x' : ''}`));
  if (mode === 'library' && job.clips[0]?.thumbnail) {
    const cover = el('img', 'library-project-thumb');
    cover.src = `/api/jobs/${job.id}/clips/${job.clips[0].thumbnail}`;
    cover.alt = '';
    cover.loading = 'lazy';
    head.append(cover);
  }
  const state = el('div', 'job-state');
  state.append(el('span', `job-status ${job.status === 'failed' ? 'failed' : ''}`, job.status));
  if (inProgress) state.append(el('strong', 'job-progress-value', `${progress}%`));
  head.append(meta, state);
  wrapper.append(head);
  if (job.batch_id)
    wrapper.append(el('span', 'clip-tag', 'Batch source ' + job.batch_index + '/' + job.batch_total));
  if (job.status === 'failed') wrapper.append(el('p', 'error', job.error || 'This job failed.'));
  if (inProgress) {
    const bar = el('div', 'progress-bg');
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-label', `${job.status} progress`);
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', '100');
    bar.setAttribute('aria-valuenow', String(progress));
    const fill = el('div', 'progress-fill');
    fill.style.width = `${progress}%`;
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
      const item = el('div', mode === 'library' ? 'clip library-clip' : 'clip');
      const video = el('video');
      video.controls = true;
      video.preload = 'none';
      video.src = `/api/jobs/${job.id}/clips/${clip.filename}`;
      if (clip.thumbnail) video.poster = `/api/jobs/${job.id}/clips/${clip.thumbnail}`;
      const description = el('div', 'clip-info');
      const media = el('div', 'clip-media');
      media.append(video);
      const clipNumber = String(clip.number).padStart(2, '0');
      const startTime = `${Math.floor(clip.start / 60)}:${String(Math.floor(clip.start % 60)).padStart(2, '0')}`;
      if (mode === 'library') {
        media.append(el('span', 'clip-duration', `${Math.round(clip.duration)}s`));
        description.append(el('span', 'clip-eyebrow', `CLIP ${clipNumber}`));
        description.append(el('h4', 'clip-title', clip.title || `Clip ${clipNumber}`));
        description.append(el('span', 'clip-meta', `Starts at ${startTime} · ${clip.duration}s · ${clip.aspect_ratio || job.options.aspect_ratio}`));
      } else {
        description.append(el('strong', '', `Clip ${clipNumber}`),
          el('span', '', `${startTime} · ${clip.duration}s`));
      }
      const link = el('a', 'clip-save-link', '↓ Download MP4');
      link.href = video.src;
      link.download = clip.filename;
      if (mode === 'library') {
        const quickActions = el('div', 'clip-quick-actions');
        quickActions.append(link);
        description.append(quickActions);
      } else description.append(link);
      if (clip.title && mode !== 'library')
        description.append(el('p', 'clip-ai-title', clip.title));
      const audit = clip.quality_report;
      if (audit && (mode === 'library' || mode === 'publishing')) {
        const quality = el('div', 'quality-summary');
        quality.append(el('span',
          'quality-pill ' + (audit.technical_pass ? 'quality-review' : 'quality-failed'),
          audit.technical_pass ? 'Technical checks passed · Editorial review needed'
            : 'Technical check failed · Fix before posting'));
        const details = el('details', 'quality-details');
        details.append(el('summary', '', 'View export quality report'));
        const info = el('div', 'quality-description');
        info.append(el('p', '', 'Technical integrity is checked automatically. '
          + 'Narration accuracy, creative quality, copyright, and each platform’s '
          + 'publishing requirements still need verification.'));
        const issues = [...(audit.technical_issues || []), ...(audit.review_flags || [])];
        for (const flag of issues) {
          info.append(el('div', 'quality-issue',
            String(flag).replaceAll('_', ' ')));
        }
        details.append(info);
        quality.append(details);
        description.append(quality);
      }
      const tags = el('div', 'clip-tag-list');
      const tagTarget = mode === 'library' ? tags : description;
      if (mode === 'library') description.append(tags);
      if (clip.reason) description.append(el('p', 'clip-ai-reason', clip.reason));
      if (clip.captions_added) tagTarget.append(el('span', 'clip-tag', 'CC · Captions burned in'));
      if (clip.context_added) tagTarget.append(el('span', 'clip-tag', 'Scene-aware text'));
      if (clip.voiceover_added) tagTarget.append(el('span', 'clip-tag', '♬ Commentator voiceover'));
      if (clip.hook_style === 'headline') tagTarget.append(el('span', 'clip-tag', '✦ Opening hook'));
      if (clip.voice_marker) tagTarget.append(el('span', 'clip-tag', '◉ Spoken clip marker'));
      if (clip.brand_text) tagTarget.append(el('span', 'clip-tag', '◇ ' + clip.brand_text));
      if (clip.face_tracking) tagTarget.append(el('span', 'clip-tag', '◉ Face-aware crop'));
      if (clip.framing === 'split') tagTarget.append(el('span', 'clip-tag', 'Two-person split'));
      else if (clip.framing === 'smart') tagTarget.append(el('span', 'clip-tag', 'Centered fallback'));
      if (clip.revision_count) tagTarget.append(el('span', 'clip-tag', 'Revision ' + clip.revision_count));
      if (clip.revision_state === 'queued' || clip.revision_state === 'rendering')
        description.append(el('p', 'editor-progress', '⌁ Rendering your edited version…'));
      if (clip.revision_state === 'failed')
        description.append(el('p', 'error', clip.revision_error || 'Clip revision failed. The previous version is still available.'));
      if (clip.script) {
        const scriptDetails = el('details', 'script-details');
        const toggle = el('summary', '', clip.storyboard?.length
          ? 'Read timed video-grounded script' : 'Read AI commentary script');
        const scriptText = el('div', 'script-copy');
        if (clip.storyboard?.length) {
          const basis = clip.analysis_basis === 'video_frames_and_speech'
            ? 'Video frames + spoken content' : 'Spoken content only';
          scriptText.append(el('p', 'script-basis', basis +
            ' · review AI descriptions against the actual video.'));
          for (const beat of clip.storyboard) {
            const time = (n) => {
              const seconds = Math.round(n);
              return String(Math.floor(seconds / 60)).padStart(2, '0') +
                ':' + String(seconds % 60).padStart(2, '0');
            };
            const row = el('div', 'script-beat');
            row.append(el('strong', 'script-beat-time',
              time(beat.start) + '–' + time(beat.end)),
              el('span', '', beat.text || '[No reliable source evidence]'));
            scriptText.append(row);
          }
        } else scriptText.textContent = clip.script;
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
        const copyDraft = clip.post_copy;
        const draftLine = (copy) => [
          copy.description || '', (copy.hashtags || []).join(' ')
        ].filter(Boolean).join('\n\n');
        const copyButton = el('button', 'small-action', '✦ Draft post copy from video');
        copyButton.type = 'button';
        copyButton.disabled = ['queued', 'generating'].includes(clip.post_copy_state);
        copyButton.addEventListener('click', async () => {
          copyButton.disabled = true;
          try {
            const response = await fetch(
              '/api/jobs/' + job.id + '/clips/' + clip.number + '/post-copy',
              {method: 'POST'}
            );
            const data = await response.json();
            if (!response.ok) throw new Error(
              typeof data.detail === 'string' ? data.detail : 'Could not start AI post draft');
            notify('Local AI is preparing a title and description. Refresh Publishing to review.');
            await refresh();
          } catch (error) {
            notify(error.message);
            copyButton.disabled = false;
          }
        });
        description.append(copyButton);
        if (clip.post_copy_state === 'queued' ||
            clip.post_copy_state === 'generating')
          description.append(el('p', 'helper-note',
            'Local AI is preparing publishing copy from the selected clip…'));
        if (clip.post_copy_state === 'failed')
          description.append(el('p', 'error',
            clip.post_copy_error || 'Local publishing copy generation failed'));
        if (copyDraft) {
          const draftBox = el('div', 'post-copy-suggestion');
          draftBox.append(el('strong', '', 'AI post draft · review and edit'));
          draftBox.append(el('p', '', copyDraft.title));
          draftBox.append(el('p', '', draftLine(copyDraft)));
          const copyAction = el('button', 'small-action', 'Copy draft text');
          copyAction.type = 'button';
          copyAction.addEventListener('click', () => {
            navigator.clipboard.writeText(
              copyDraft.title + '\n\n' + draftLine(copyDraft)
            ).then(() => notify('Publishing draft copied.'))
              .catch(() => notify('Copy unavailable in this browser.'));
          });
          draftBox.append(copyAction);
          description.append(draftBox);
        }
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
          titleField.value = copyDraft?.title || clip.title || `Clip ${clip.number}`;
          titleField.placeholder = 'Post title';
          const captionField = el('textarea');
          captionField.maxLength = 2200;
          captionField.rows = 2;
          captionField.placeholder = 'Description / hashtags (optional)';
          captionField.value = copyDraft ? draftLine(copyDraft) : '';
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
          scheduledTitle.value = copyDraft?.title || clip.title || 'Clip ' + clip.number;
          scheduledTitle.placeholder = 'Publication title';

          const scheduledDescription = el('textarea');
          scheduledDescription.rows = 2;
          scheduledDescription.maxLength = 2200;
          scheduledDescription.placeholder = 'Optional description / hashtags';
          scheduledDescription.value = copyDraft ? draftLine(copyDraft) : '';
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
          const scheduleGuidance = el('p', 'helper-note',
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
            scheduleGuidance, savePreset, scheduleSubmit,
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
      item.append(media, description);
      grid.append(item);
    }
    if (mode === 'library') {
      const gallery = el('details', 'library-folder');
      gallery.open = libraryExpanded.has(job.id);
      const toggle = el('summary', 'library-folder-toggle');
      toggle.append(el('span', 'library-folder-label', 'Preview & refine clips'),
        el('span', 'library-folder-count', `${job.clips.length} clip${job.clips.length === 1 ? '' : 's'}`),
        el('span', 'library-chevron', '⌄'));
      gallery.addEventListener('toggle', () => {
        if (gallery.open) libraryExpanded.add(job.id);
        else libraryExpanded.delete(job.id);
      });
      gallery.append(toggle, grid);
      wrapper.append(gallery);
    } else wrapper.append(grid);
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

function renderLibrary() {
  if (currentPage !== 'library') return;
  const query = $('library-search').value.trim().toLocaleLowerCase();
  const filter = $('library-filter').value;
  const sort = $('library-sort').value;
  const allClips = libraryJobs.flatMap((job) => job.clips || []);
  $('library-total-clips').textContent = String(allClips.length);
  $('library-total-sources').textContent = String(libraryJobs.length);
  $('library-total-narrated').textContent = String(
    allClips.filter((clip) => clip.script || clip.voiceover_added).length);
  const visible = libraryJobs.filter((job) => {
    if (filter === 'ready' && (job.status !== 'completed' || !job.clips.length)) return false;
    if (filter === 'processing' && !['queued', 'downloading', 'analyzing',
      'transcribing', 'selecting', 'rendering'].includes(job.status)) return false;
    if (!query) return true;
    const searchable = [job.label, job.options?.content_niche,
      ...(job.clips || []).flatMap((clip) =>
        [clip.title, clip.reason, clip.script, clip.brand_text])].join(' ').toLocaleLowerCase();
    return searchable.includes(query);
  });
  visible.sort((a, b) => {
    if (sort === 'clips') return b.clips.length - a.clips.length ||
      new Date(b.created_at) - new Date(a.created_at);
    return sort === 'oldest'
      ? new Date(a.created_at) - new Date(b.created_at)
      : new Date(b.created_at) - new Date(a.created_at);
  });
  if (!libraryInitialized && visible.length) {
    libraryExpanded.add(visible[0].id);
    libraryInitialized = true;
  }
  const count = visible.reduce((sum, job) => sum + job.clips.length, 0);
  $('library-summary').textContent =
    `${visible.length} project${visible.length === 1 ? '' : 's'} · ${count} clip${count === 1 ? '' : 's'}`;
  const target = $('jobs-library');
  target.replaceChildren();
  if (!visible.length) {
    const empty = el('div', 'empty library-empty');
    empty.append(el('div', 'empty-graphic', query || filter !== 'all' ? '⌕' : '▤'),
      el('strong', '', query || filter !== 'all' ? 'No projects match your filters' :
        'Your clip library is ready for its first project'),
      el('span', '', query || filter !== 'all' ? 'Try a different search or project status.' :
        'Create a video in Studio to see your clips here.'));
    if (query || filter !== 'all') {
      const clear = el('button', 'small-action', 'Clear filters');
      clear.type = 'button';
      clear.addEventListener('click', () => {
        $('library-search').value = '';
        $('library-filter').value = 'all';
        renderLibrary();
      });
      empty.append(clear);
    } else {
      const create = el('a', 'small-action primary-action', '✦ Create clips');
      create.href = '/studio';
      empty.append(create);
    }
    target.append(empty);
  } else target.append(...visible.map((job) => card(job, 'library')));
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
    const shown = currentPage === 'publishing'
      ? jobs.filter((job) => job.status === 'completed') : jobs;
    if (currentPage === 'library') {
      libraryJobs = shown;
      renderLibrary();
    } else {
      target.replaceChildren();
      if (!shown.length) {
        const empty = el('div', 'empty');
        empty.append(el('div', 'empty-graphic', '▤'),
          el('strong', '', currentPage === 'activity' ?
            'No processing jobs yet' : 'No clips ready to publish'),
          el('span', '', 'Create clips in Studio to populate this page.'));
        target.append(empty);
      } else target.append(...shown.map((job) => card(job, currentPage)));
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
  narration_basis: 'narration-basis',
  content_niche: 'content-niche',
  clip_focus: 'clip-focus',
  use_voice_markers: 'use-voice-markers',
  brand_text: 'brand-text',
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
    } else if (item.type === 'checkbox') item.checked = Boolean(defaults[key]);
    else item.value = String(defaults[key]);
  }
  if (!prefix) {
    if ($('framing').value === 'split') $('aspect-ratio').value = '9:16';
    renderFraming();
    $('caption-style').dispatchEvent(new Event('change'));
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
      if (settings.vision && $('narration-basis').value === 'video') {
        $('narration-notice').textContent += settings.vision.installed
          ? ' Vision model ' + settings.vision.model + ' is installed.'
          : ' Vision model unavailable: ' + settings.vision.note;
      }
      if (!settings.narration.elevenlabs_configured && $('narration').value === 'narrated') {
        $('narration-notice').textContent +=
          ' ElevenLabs is not connected. Choose contextual text or configure it in Settings.';
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
      $('settings-vision-status').textContent = settings.vision?.installed
        ? 'Vision model: ' + settings.vision.model + ' · installed and ready.'
        : 'Vision model: ' + (settings.vision?.model || 'not configured') +
          ' · ' + (settings.vision?.note || 'Status unknown.');
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

async function loadCreatorProfiles() {
  if (currentPage !== 'studio') return;
  try {
    const response = await fetch('/api/creator/profiles', {cache: 'no-store'});
    if (!response.ok) return;
    creatorProfiles = await response.json();
    const selector = $('creator-profile-select');
    selector.replaceChildren(el('option', '', 'Custom · not saved'));
    for (const profile of creatorProfiles) {
      const option = el('option', '', profile.name);
      option.value = profile.name;
      selector.append(option);
    }
  } catch (error) { notify('Could not load saved creator presets.'); }
}

function applyCreatorProfile() {
  const name = $('creator-profile-select').value;
  const profile = creatorProfiles.find((item) => item.name === name);
  if (!profile) return;
  applyDefaults(profile, '');
  notify('Creator profile applied. Confirm settings before starting a job.');
}

async function saveCreatorProfile() {
  const name = prompt('Save this channel style as (up to 40 characters):');
  if (!name || !name.trim()) return;
  const payload = {
    name: name.trim(), content_niche: $('content-niche').value,
    clip_focus: $('clip-focus').value.trim(),
    caption_style: $('caption-style').value,
    framing: $('framing').value, effect: $('effect').value,
    hook_style: $('hook-style').value,
    use_voice_markers: $('use-voice-markers').checked,
    brand_text: $('brand-text').value.trim()
  };
  try {
    const response = await fetch('/api/creator/profiles', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(
      typeof result.detail === 'string' ? result.detail : 'Cannot save profile');
    await loadCreatorProfiles();
    $('creator-profile-select').value = result.name;
    notify('Channel style saved. It can be reused for future batches.');
  } catch (error) { notify(error.message); }
}

async function savePreferences(event) {
  event.preventDefault();
  const data = {};
  for (const [key, field] of Object.entries(settingsIds)) {
    const element = $('settings-' + field);
    const raw = element.type === 'checkbox' ? element.checked : element.value;
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

function clearPostingPresetForm() {
  $('posting-preset-form').reset();
  $('posting-preset-feedback').textContent = '';
}

function editPostingPreset(preset) {
  $('posting-preset-name').value = preset.name;
  $('posting-preset-visibility').value = preset.visibility || 'private';
  $('posting-preset-description').value = preset.description || '';
  for (const input of $('posting-preset-platforms').querySelectorAll('input')) {
    input.checked = preset.platforms.includes(input.value);
  }
  $('posting-preset-feedback').textContent = `Editing ${preset.name}. Save to update it.`;
  $('posting-preset-name').focus();
}

function renderPostingSettings() {
  const connections = $('posting-platform-connections');
  connections.replaceChildren();
  const checkboxes = $('posting-preset-platforms');
  checkboxes.replaceChildren();
  for (const platform of platforms) {
    const item = el('div', 'connection-card');
    item.append(el('strong', '', platform.name),
      el('span', '', platform.configured ? 'Connected on server' : 'Needs server credentials'),
      el('small', '', platform.mode === 'draft'
        ? 'Creates a draft; finish posting in the app' : 'Direct publishing when authorized'));
    connections.append(item);
    const label = el('label');
    const input = el('input');
    input.type = 'checkbox';
    input.value = platform.id;
    label.append(input, document.createTextNode(platform.name));
    checkboxes.append(label);
  }
  const list = $('posting-preset-list');
  list.replaceChildren();
  if (!publishingPresets.length) {
    list.append(el('p', 'helper-note', 'No presets saved. Create a channel-specific preset on the left.'));
  }
  for (const preset of publishingPresets) {
    const item = el('div', 'posting-preset-item');
    const details = el('div');
    details.append(el('strong', '', preset.name),
      el('small', '', `${preset.platforms.join(', ')} · ${preset.visibility || 'private'}`));
    const actions = el('div', 'posting-preset-actions');
    const edit = el('button', 'small-action', 'Edit');
    edit.type = 'button';
    edit.addEventListener('click', () => editPostingPreset(preset));
    const remove = el('button', 'small-action', 'Delete');
    remove.type = 'button';
    remove.addEventListener('click', async () => {
      if (!confirm(`Delete posting preset "${preset.name}"? Scheduled posts are not affected.`)) return;
      try {
        const response = await fetch('/api/publishing/presets/' + encodeURIComponent(preset.name),
          {method: 'DELETE'});
        if (!response.ok) throw new Error('Could not delete posting preset');
        $('posting-preset-feedback').textContent = `Deleted ${preset.name}.`;
        if ($('posting-preset-name').value === preset.name) clearPostingPresetForm();
        await loadPostingSettings();
      } catch (error) { $('posting-preset-feedback').textContent = error.message; }
    });
    actions.append(edit, remove);
    item.append(details, actions);
    list.append(item);
  }
}

async function loadPostingSettings() {
  if (currentPage !== 'posting-settings') return;
  try {
    const [platformResponse, presetResponse] = await Promise.all([
      fetch('/api/publishing/platforms', {cache: 'no-store'}),
      fetch('/api/publishing/presets', {cache: 'no-store'})
    ]);
    if (!platformResponse.ok || !presetResponse.ok) throw new Error('Cannot load posting settings');
    platforms = await platformResponse.json();
    publishingPresets = await presetResponse.json();
    renderPostingSettings();
  } catch (error) { $('posting-preset-feedback').textContent = error.message; }
}

$('posting-preset-clear').addEventListener('click', clearPostingPresetForm);
$('posting-preset-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const selected = [...$('posting-preset-platforms').querySelectorAll('input:checked')]
    .map((input) => input.value);
  if (!selected.length) {
    $('posting-preset-feedback').textContent = 'Select at least one platform.';
    return;
  }
  const payload = {
    name: $('posting-preset-name').value.trim(), platforms: selected,
    description: $('posting-preset-description').value.trim(),
    visibility: $('posting-preset-visibility').value
  };
  try {
    const response = await fetch('/api/publishing/presets', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string'
      ? result.detail : 'Could not save posting preset');
    await loadPostingSettings();
    $('posting-preset-feedback').textContent = `${result.name} saved. Available in Publishing.`;
  } catch (error) { $('posting-preset-feedback').textContent = error.message; }
});

loadSettings();
loadCreatorProfiles();
loadPostingSettings();
refresh();
setInterval(() => {
  // Library is refreshed explicitly: don't interrupt paused previews, filters, or expanded folders.
  if (!['publishing', 'activity'].includes(currentPage)) return;
  // Do not recreate a video player while someone is watching it or editing a post.
  if (![...document.querySelectorAll('video')].some((v) => !v.paused) &&
      ![...document.querySelectorAll('.publish-panel')].some((p) => !p.hidden) &&
      ![...document.querySelectorAll('.clip-editor')].some((p) => p.open)) {
    refresh();
  }
}, 5000);
