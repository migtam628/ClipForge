/* Media geometry comes from decoded playback metadata, not export presets.
   Keep the helper standalone so it can also be tested without a browser. */
(function (root) {
  const COMMON = [
    ['9:16', 9, 16], ['2:3', 2, 3], ['3:4', 3, 4], ['4:5', 4, 5],
    ['1:1', 1, 1], ['5:4', 5, 4], ['4:3', 4, 3], ['3:2', 3, 2],
    ['16:9', 16, 9], ['21:9', 21, 9]
  ];

  function ratioValue(value) {
    const match = /^(\d+):(\d+)$/.exec(String(value || ''));
    if (!match || !Number(match[2])) return null;
    const ratio = Number(match[1]) / Number(match[2]);
    return Number.isFinite(ratio) && ratio > 0 ? ratio : null;
  }

  function dimensions(width, height) {
    width = Number(width);
    height = Number(height);
    if (!Number.isFinite(width) || !Number.isFinite(height) ||
        width <= 0 || height <= 0) return null;
    const ratio = width / height;
    const known = COMMON.find(([, x, y]) =>
      Math.abs(ratio - x / y) / (x / y) < 0.006);
    const label = known ? known[0] : `${ratio.toFixed(2)}:1`;
    return {
      width, height, ratio, label,
      orientation: ratio < 0.98 ? 'portrait' : ratio > 1.02 ? 'landscape' : 'square'
    };
  }

  function applyPreset(stage, preset) {
    const ratio = ratioValue(preset);
    if (stage && ratio) stage.style.setProperty('--media-aspect', String(ratio));
  }

  function observe(video, {stage = video, label = null, preset = '', onSize = null} = {}) {
    applyPreset(stage, preset);
    const update = () => {
      const info = dimensions(video.videoWidth, video.videoHeight);
      if (!info) return;
      if (stage) {
        stage.style.setProperty('--media-aspect', String(info.ratio));
        stage.dataset.mediaOrientation = info.orientation;
      }
      video.dataset.playbackRatio = info.label;
      if (label) label.textContent = `${info.label} · ${info.width} × ${info.height}`;
      if (onSize) onSize(info);
    };
    video.addEventListener('loadedmetadata', update);
    if (video.readyState >= 1) update();
    return update;
  }

  const API = {dimensions, ratioValue, applyPreset, observe};
  root.ClipForgeMedia = API;
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
})(typeof globalThis !== 'undefined' ? globalThis : this);
