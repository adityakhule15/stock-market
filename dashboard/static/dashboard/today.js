(function () {
  var INDEX_MAP = {
    NIFTY: '^NSEI',
    SENSEX: '^BSESN',
    BANKNIFTY: '^NSEBANK',
    INDIAVIX: '^INDIAVIX',
  };
  function csrf() {
    return window.SS_CSRF || (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
  }

  function fmt(n, d) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toLocaleString('en-IN', {
      minimumFractionDigits: d == null ? 2 : d,
      maximumFractionDigits: d == null ? 2 : d,
    });
  }

  function setChg(wrapId, textId, chg, pct) {
    var wrap = document.getElementById(wrapId);
    var el = document.getElementById(textId);
    if (!el) return;
    var up = (chg || 0) >= 0;
    el.textContent = (up ? '▲ ' : '▼ ') + fmt(Math.abs(chg)) + ' (' + (up ? '+' : '') + fmt(pct) + '%)';
    if (wrap) {
      wrap.classList.remove('up', 'down');
      wrap.classList.add(up ? 'up' : 'down');
    }
  }

  async function loadQuote(uiKey, yahooSym) {
    try {
      var resp = await fetch('/api/quotes/' + encodeURIComponent(yahooSym) + '/', { cache: 'no-store' });
      var data = await resp.json();
      var bar = data.bar || {};
      var close = bar.close != null ? bar.close : bar.c;
      var open = bar.open != null ? bar.open : (bar.o != null ? bar.o : close);
      var chg = close != null && open != null ? close - open : 0;
      var pct = open ? (chg / open) * 100 : 0;
      var prefix = uiKey === 'BANKNIFTY' ? 'bank' : uiKey === 'INDIAVIX' ? 'vix' : uiKey.toLowerCase();
      var priceEl = document.getElementById(prefix + '-price');
      if (priceEl) priceEl.textContent = fmt(close);
      setChg(prefix + '-chg-wrap', prefix + '-chg', chg, pct);
      var ageEl = document.getElementById(prefix + '-age');
      if (ageEl && data.data_age_min != null) ageEl.textContent = data.data_age_min + 'm ago';
      if (uiKey === 'INDIAVIX') {
        var st = document.getElementById('vix-status');
        if (st) {
          st.textContent = close >= 18 ? 'Elevated' : close <= 13 ? 'Calm' : 'Normal';
          st.style.color = close >= 18 ? 'var(--amber)' : 'var(--muted)';
        }
      }
    } catch (e) { /* keep placeholder */ }
  }

  function loadIndexStrip() {
    Object.keys(INDEX_MAP).forEach(function (k) { loadQuote(k, INDEX_MAP[k]); });
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

  document.getElementById('index-strip')?.addEventListener('click', function (e) {
    var card = e.target.closest('.index-card[data-symbol]');
    if (!card) return;
    var sym = card.getAttribute('data-symbol');
    if (sym === 'INDIAVIX') return;
    document.querySelectorAll('#index-strip .index-card').forEach(function (c) {
      c.classList.toggle('active', c.getAttribute('data-symbol') === sym);
    });
  });

  var saved = sessionStorage.getItem('ss_tab') || 'all';
  setTab(saved);
  loadIndexStrip();

  var timer = null;
  function poll() {
    if (document.hidden) return;
    var tab = sessionStorage.getItem('ss_tab') || 'all';
    if (window.htmx) htmx.ajax('GET', '/partials/signals/?tab=' + tab, { target: '#signals', swap: 'innerHTML' });
    loadIndexStrip();
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
    if (st) st.textContent = 'Planning tomorrow… (scanning wallet-fit ideas)';
    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var abortTimer = ctrl ? setTimeout(function () { ctrl.abort(); }, 90000) : null;
    try {
      var resp = await fetch('/api/advisor/tomorrow/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ rescan: true, ai: false }),
        signal: ctrl ? ctrl.signal : undefined,
      });
      var data = await resp.json();
      if (abortTimer) clearTimeout(abortTimer);
      if (!resp.ok || data.ok === false) {
        if (st) st.textContent = data.error || data.summary || ('Failed (' + resp.status + ')');
        toast(st.textContent);
        return;
      }
      var msg = data.headline || data.summary || (data.verdict + ' · ' + (data.picks || []).length + ' pick(s)');
      if (data.warnings && data.warnings.length) msg += ' · ' + data.warnings[0];
      if (st) st.textContent = msg;
      toast(msg);
      poll();
    } catch (e) {
      if (abortTimer) clearTimeout(abortTimer);
      var err = (e && e.name === 'AbortError')
        ? 'Timed out after 90s — check the SERVER window for errors'
        : String(e);
      if (st) st.textContent = err;
      toast(err);
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
