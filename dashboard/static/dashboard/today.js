(function () {
  function csrf() {
    return window.SS_CSRF || (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
  }

  function setTab(tab) {
    sessionStorage.setItem('ss_tab', tab);
    document.querySelectorAll('#board-tabs button').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-tab') === tab);
    });
    var host = document.getElementById('signals');
    if (host) {
      host.setAttribute('hx-get', '/partials/signals/?tab=' + tab);
      if (window.htmx) htmx.ajax('GET', '/partials/signals/?tab=' + tab, { target: '#signals', swap: 'innerHTML' });
    }
  }

  document.getElementById('board-tabs')?.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-tab]');
    if (!btn) return;
    setTab(btn.getAttribute('data-tab'));
  });

  var saved = sessionStorage.getItem('ss_tab') || 'all';
  setTab(saved);

  // Poll only while tab visible — every 30s
  var timer = null;
  function poll() {
    if (document.hidden) return;
    var tab = sessionStorage.getItem('ss_tab') || 'all';
    if (window.htmx) htmx.ajax('GET', '/partials/signals/?tab=' + tab, { target: '#signals', swap: 'innerHTML' });
  }
  function startPoll() {
    stopPoll();
    timer = setInterval(poll, 30000);
  }
  function stopPoll() {
    if (timer) clearInterval(timer);
    timer = null;
  }
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stopPoll();
    else { poll(); startPoll(); }
  });
  startPoll();

  document.getElementById('btn-scan')?.addEventListener('click', async function () {
    var st = document.getElementById('scan-status');
    if (st) st.textContent = 'Scanning…';
    try {
      var resp = await fetch('/api/signals/scan/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ mode: 'all' }),
      });
      var data = await resp.json();
      if (st) st.textContent = data.ok ? ('Created ' + (data.created || 0)) : (data.error || 'failed');
      poll();
    } catch (e) {
      if (st) st.textContent = String(e);
    }
  });

  document.getElementById('btn-tomorrow')?.addEventListener('click', async function () {
    var st = document.getElementById('scan-status');
    if (st) st.textContent = 'Planning tomorrow…';
    try {
      var resp = await fetch('/api/advisor/tomorrow/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({}),
      });
      var data = await resp.json();
      if (st) st.textContent = data.ok ? 'Tomorrow plan ready' : (data.error || 'failed');
      toast(data.summary || data.bot || st.textContent);
    } catch (e) {
      if (st) st.textContent = String(e);
    }
  });

  document.addEventListener('change', function (e) {
    if (!e.target.classList.contains('ck')) return;
    var id = e.target.getAttribute('data-id');
    var boxes = document.querySelectorAll('.ck[data-id="' + id + '"]');
    var all = true;
    boxes.forEach(function (b) { if (!b.checked) all = false; });
    var btn = document.getElementById('copy-' + id);
    if (btn) btn.disabled = !all;
  });

  window.copyGrowwTicket = function (id, detail, mode) {
    var text = detail || ('StockScope ticket #' + id);
    navigator.clipboard.writeText(text).then(function () {
      toast('Copied for Groww');
    }).catch(function () { toast('Copy failed'); });
  };

  window.confirmOrder = async function (id, placed) {
    var fill = null;
    if (placed) {
      fill = prompt('Groww fill price (₹)?');
      if (fill === null) return;
    }
    try {
      var resp = await fetch('/api/signals/' + id + '/mark-taken/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({
          order_confirmed: !!placed,
          fill_price: fill ? parseFloat(fill) : null,
          mode: 'paper',
        }),
      });
      var data = await resp.json();
      toast(data.ok ? (placed ? 'Position opened' : 'Skipped') : (data.error || 'failed'));
      poll();
    } catch (e) {
      toast(String(e));
    }
  };

  window.trackPractice = async function (id) {
    try {
      var resp = await fetch('/api/signals/' + id + '/mark-taken/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ order_confirmed: true, mode: 'paper', practice: true }),
      });
      // Fallback: manual Groww dummy if mark-taken rejects
      var data = await resp.json();
      if (!data.ok) {
        resp = await fetch('/api/positions/manual-groww/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
          body: JSON.stringify({ signal_id: id, is_dummy: true }),
        });
        data = await resp.json();
      }
      toast(data.ok ? 'Tracked as practice (wallet unchanged)' : (data.error || 'failed'));
      poll();
    } catch (e) {
      toast(String(e));
    }
  };
})();
