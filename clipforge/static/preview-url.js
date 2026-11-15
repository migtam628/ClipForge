// Keep embedded previews separate from import URLs: only an exact, supported
// YouTube hostname and an 11-character video ID may reach the external player.
(function (root) {
  function parseYouTubePreview(raw) {
    let url;
    try {
      url = new URL(String(raw).trim());
    } catch {
      return null;
    }
    if (url.protocol !== 'https:' || url.username || url.password) return null;
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    const youtubeHosts = new Set([
      'youtube.com', 'www.youtube.com', 'm.youtube.com',
      'music.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com'
    ]);
    let id = null;
    if (host === 'youtu.be' || host === 'www.youtu.be') {
      const parts = url.pathname.split('/').filter(Boolean);
      if (parts.length === 1) id = parts[0];
    } else if (youtubeHosts.has(host)) {
      const parts = url.pathname.split('/').filter(Boolean);
      if (parts.length === 1 && parts[0] === 'watch') {
        id = url.searchParams.get('v');
      } else if (parts.length === 2 && ['shorts', 'live', 'embed'].includes(parts[0])) {
        id = parts[1];
      }
    }
    if (!/^[a-zA-Z0-9_-]{11}$/.test(id || '')) return null;
    return {
      id,
      embedUrl: `https://www.youtube-nocookie.com/embed/${id}?rel=0`,
      thumbnailUrl: `https://i.ytimg.com/vi/${id}/hqdefault.jpg`
    };
  }

  root.parseYouTubePreview = parseYouTubePreview;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {parseYouTubePreview};
  }
})(typeof window !== 'undefined' ? window : globalThis);
