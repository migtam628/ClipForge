/* ClipForge Analytics — separate, auditable user-entered creator data UI. */
(() => {
  if (location.pathname !== '/analytics') return;
  const $a = (id) => document.getElementById(id);
  const make = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = String(value);
    return node;
  };
  const numeric = (value) => Number(value || 0).toLocaleString();
  const money = (value) => '$' + Number(value || 0).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  });

  async function loadAnalytics() {
    try {
      const [summaryRes, jobsRes] = await Promise.all([
        fetch('/api/analytics/summary', {cache: 'no-store'}),
        fetch('/api/jobs', {cache: 'no-store'})
      ]);
      if (!summaryRes.ok || !jobsRes.ok)
        throw new Error('Analytics data is unavailable on this server.');
      const summary = await summaryRes.json();
      const jobs = await jobsRes.json();
      const clipSelector = $a('analytics-clip');
      const previous = clipSelector.value;
      const external = make('option', '', 'External post / no associated clip');
      external.value = '';
      clipSelector.replaceChildren(external);
      for (const job of jobs) {
        for (const clip of job.clips || []) {
          const option = make('option', '',
            (job.label || 'Video').slice(0, 56) + ' · Clip ' + clip.number);
          option.value = job.id + '|' + clip.filename;
          clipSelector.append(option);
        }
      }
      if ([...clipSelector.options].some((opt) => opt.value === previous))
        clipSelector.value = previous;

      const stats = $a('analytics-stats');
      stats.replaceChildren();
      for (const [value, label] of [
        [numeric(summary.tracked_posts), 'Manually tracked posts'],
        [numeric(summary.views), 'Self-reported views'],
        [numeric(summary.engagements), 'Self-reported engagements'],
        [money(summary.earnings_usd), 'Reported actual payouts']
      ]) {
        const item = make('div', 'stat-item');
        item.append(make('strong', '', value), make('span', '', label));
        stats.append(item);
      }

      const platformPanel = $a('analytics-platform-results');
      platformPanel.replaceChildren();
      const platformRows = summary.by_platform || [];
      if (!platformRows.length)
        platformPanel.append(make('p', 'analytics-empty',
          'Add your first metric snapshot to compare platform performance.'));
      const maxViews = Math.max(1, ...platformRows.map((row) => row.views));
      for (const row of platformRows) {
        const card = make('div', 'analytics-platform-row');
        const heading = make('div');
        heading.append(make('strong', '', row.platform.toUpperCase()),
                       make('span', '', row.posts + ' post(s)'));
        const bar = make('div', 'analytics-platform-bar');
        const fill = make('div', 'analytics-platform-fill');
        fill.style.width = Math.max(0,
          Math.min(100, row.views / maxViews * 100)) + '%';
        bar.append(fill);
        card.append(heading, bar, make('span', '',
          numeric(row.views) + ' reported views · ' +
          money(row.earnings_usd) + ' reported payouts'));
        platformPanel.append(card);
      }

      const recent = $a('analytics-records');
      recent.replaceChildren();
      if (!summary.latest.length)
        recent.append(make('p', 'analytics-empty',
          'No records yet. Enter your actual platform metrics and payouts.'));
      for (const record of summary.latest) {
        const item = make('div', 'analytics-record-item');
        item.append(make('strong', '',
          record.platform.toUpperCase() + ' · ' +
          (record.campaign_name || record.filename || 'External post')));
        item.append(make('span', '',
          numeric(record.views) + ' views · ' + money(record.earnings_usd) +
          ' · ' + (record.campaign_status === 'none'
            ? 'No campaign' : record.campaign_status)));
        item.append(make('small', '',
          'Self-reported · ' + new Date(record.captured_at).toLocaleString()));
        if (record.post_url && record.post_url.startsWith('https://')) {
          const link = make('a', '', 'View source post');
          link.href = record.post_url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          item.append(link);
        }
        const remove = make('button', 'small-action', 'Delete snapshot');
        remove.type = 'button';
        remove.addEventListener('click', async () => {
          if (!confirm('Delete this snapshot? An older entry may become the latest.'))
            return;
          const result = await fetch('/api/analytics/records/' + record.id,
            {method: 'DELETE'});
          if (result.ok) await loadAnalytics();
          else notify('Unable to delete this snapshot.');
        });
        item.append(remove);
        recent.append(item);
      }

      const publicationPanel = $a('analytics-publication-states');
      publicationPanel.replaceChildren();
      const publicationRows = Object.entries(summary.publication_states || {});
      if (!publicationRows.length)
        publicationPanel.append(make('p', 'analytics-empty',
          'No internal publishing records yet.'));
      for (const [state, count] of publicationRows)
        publicationPanel.append(make('span', 'analytics-state-pill',
          state + ' · ' + numeric(count)));
    } catch (error) {
      notify(error.message);
    }
  }

  async function saveAnalytics(event) {
    event.preventDefault();
    const selected = $a('analytics-clip').value;
    const [job_id, filename] = selected ? selected.split('|') : ['', ''];
    const post_url = $a('analytics-post-url').value.trim();
    if (!job_id && !post_url) {
      notify('Choose a ClipForge clip or enter an HTTPS post URL.');
      return;
    }
    const value = $a('analytics-earnings').value;
    if (value === '' || !Number.isFinite(Number(value)) || Number(value) < 0) {
      notify('Enter an actual nonnegative payout in USD, or zero.');
      return;
    }
    const payload = {
      platform: $a('analytics-platform').value,
      post_url, job_id, filename,
      views: Number($a('analytics-views').value),
      likes: Number($a('analytics-likes').value),
      comments: Number($a('analytics-comments').value),
      shares: Number($a('analytics-shares').value),
      saves: Number($a('analytics-saves').value),
      earnings_usd: Number(value).toFixed(2),
      campaign_name: $a('analytics-campaign').value,
      campaign_status: $a('analytics-campaign-status').value,
      evidence_url: $a('analytics-evidence').value.trim(),
      notes: $a('analytics-notes').value
    };
    const submit = $a('analytics-submit');
    submit.disabled = true;
    try {
      const response = await fetch('/api/analytics/records', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok) throw new Error(
        typeof result.detail === 'string'
          ? result.detail : 'Invalid performance entry');
      notify('Self-reported snapshot saved. No provider metrics were fetched.');
      await loadAnalytics();
    } catch (error) {
      notify(error.message);
    } finally {
      submit.disabled = false;
    }
  }

  $a('analytics-form').addEventListener('submit', saveAnalytics);
  $a('analytics-refresh').addEventListener('click', loadAnalytics);
  loadAnalytics();
})();
