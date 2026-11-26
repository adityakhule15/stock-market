(function () {
  var active = window.__PATTERNS_ACTIVE__ || "NIFTY";
  var board = {};

  function $(id) { return document.getElementById(id); }

  function csrf() {
    return window.SS_CSRF || (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";
  }

  function setStatus(msg) {
    var el = $("patterns-status");
    if (el) el.textContent = msg || "";
  }

  function setModel(info) {
    var el = $("patterns-model");
    if (!el) return;
    if (!info) {
      el.textContent = "Model: not trained yet — use Train model after Load 5y data.";
      return;
    }
    el.textContent =
      "Model: " + (info.name || "index_pattern_lgbm") + "_v" + info.version +
      " · expectancy " + (info.expectancy_R != null ? Number(info.expectancy_R).toFixed(3) : "—") +
      " · AUC " + (info.auc != null ? Number(info.auc).toFixed(3) : "—") +
      (info.n_rows != null ? (" · rows " + info.n_rows) : "") +
      (info.train_end ? (" · through " + info.train_end) : "");
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    }).then(function (r) { return r.json().then(function (d) { return { okHttp: r.ok, data: d }; }); });
  }

  function setBusy(ids, busy) {
    (ids || []).forEach(function (id) {
      var el = $(id);
      if (el) el.disabled = !!busy;
    });
  }

  function biasClass(bias) {
    if (bias === "BULLISH") return "bull";
    if (bias === "BEARISH") return "bear";
    return "flat";
  }

  function kvRows(rows) {
    return rows.map(function (row) {
      return '<div><span class="muted">' + row[0] + '</span><div class="mono">' + row[1] + "</div></div>";
    }).join("");
  }

  function renderSkip(host, call) {
    host.innerHTML =
      '<div><span class="muted">Action</span><div>' + (call.action || "SKIP") + "</div></div>" +
      '<div style="grid-column:1/-1"><span class="muted">Why</span><div>' +
      (call.reason || "No wallet-fit ticket") + "</div></div>";
  }

  function renderFnoTicket(hostId, call) {
    var host = $(hostId);
    if (!host) return;
    call = call || {};
    if (call.action !== "BUY" || !call.affordable) {
      renderSkip(host, call);
      return;
    }
    host.innerHTML = kvRows([
      ["Session", call.session_label || call.hold || "—"],
      ["Option", (call.option_type || "") + " " + (call.strike || "")],
      ["Buy LTP", "₹" + (call.buy_ltp ?? "—")],
      ["Stop", "₹" + (call.sl_ltp ?? "—")],
      ["Target", "₹" + (call.target_ltp ?? "—")],
      ["Lots", (call.lots ?? 0) + " × " + (call.lot_size ?? "")],
      ["Debit", "₹" + (call.buy_debit_rs ?? "—")],
      ["Max loss", "₹" + (call.max_loss_rs ?? "—")],
      ["Enter after", call.enter_after_ist || "09:46"],
      ["Square off", call.square_off_by_ist || "15:10"],
      ["Pricing", call.pricing || "—"],
    ]);
  }

  function renderNormalTicket(call) {
    var host = $("pat-ticket-normal");
    var linkedHost = $("pat-linked");
    if (!host) return;
    call = call || {};
    if (call.action === "SKIP" || (!call.spot_entry && call.action !== "WATCH" && call.action !== "BUY")) {
      renderSkip(host, call);
      if (linkedHost) linkedHost.innerHTML = "";
      return;
    }
    host.innerHTML = kvRows([
      ["Action", call.action || "WATCH"],
      ["Index entry", "₹" + (call.spot_entry ?? "—")],
      ["Index stop", "₹" + (call.spot_stop ?? "—")],
      ["Index target", "₹" + (call.spot_target ?? "—")],
      ["Hold", call.hold || "SWING"],
      ["Session", call.session_label || "—"],
    ]) + '<div style="grid-column:1/-1"><span class="muted">Note</span><div style="font-size:0.82rem">' +
      (call.note || call.reason || "") + "</div></div>";

    var linked = call.linked_stocks || [];
    if (!linkedHost) return;
    if (!linked.length) {
      linkedHost.innerHTML = '<li class="muted">No linked Normal CNC tickets yet — Scan on Today.</li>';
      return;
    }
    linkedHost.innerHTML = linked.map(function (s) {
      return '<li><strong>' + (s.symbol || "") + "</strong> " + (s.side || "BUY") +
        " · entry ₹" + (s.entry ?? "—") +
        " SL ₹" + (s.stop ?? "—") +
        " T1 ₹" + (s.target ?? "—") +
        " qty " + (s.qty ?? "—") +
        ' <span class="muted">p=' + (s.prob != null ? Math.round(Number(s.prob) * 100) + "%" : "—") + "</span></li>";
    }).join("");
  }

  function render(u) {
    var row = board[u] || {};
    $("pat-name").textContent = u;
    $("pat-spot").textContent = row.spot ? Number(row.spot).toFixed(2) : "—";
    $("pat-spot-src").textContent = row.spot_source ? ("source: " + row.spot_source) : "";
    var bias = row.bias || "NEUTRAL";
    var biasEl = $("pat-bias");
    biasEl.textContent = bias;
    biasEl.className = "pat-bias " + biasClass(bias);
    var up = row.prob_up_pct;
    var dn = row.prob_down_pct;
    $("pat-probs").textContent =
      "P(up) " + (up != null ? up + "%" : "—") +
      " · P(down) " + (dn != null ? dn + "%" : "—");
    $("pat-model").textContent =
      row.model_prob_up_pct != null
        ? ("Model P(up) " + row.model_prob_up_pct + "%")
        : (row.model_prob_up != null ? ("Model P(up) " + Math.round(row.model_prob_up * 1000) / 10 + "%") : "Model: not trained yet");

    var call = row.call || {};
    var kindLbl = call.kind === "next_day" ? "Next-day" : (call.kind === "normal" ? "Normal" : "Same-day");
    var actionLbl = call.action === "BUY" ? "IDEA" : (call.action || "SKIP");
    $("pat-call-action").textContent =
      kindLbl + " · " + actionLbl + (call.option_type ? (" " + call.option_type) : "") +
      (call.action === "BUY" ? " · place on Today" : "");
    $("pat-call-action").className = "pat-call-action " + biasClass(
      call.action === "BUY" || call.action === "WATCH" ? bias : "NEUTRAL"
    );
    $("pat-call-detail").textContent = call.reason || "";

    var list = $("pat-list");
    var pats = row.patterns || [];
    if (!pats.length) {
      list.innerHTML = '<li class="muted">No active patterns right now.</li>';
    } else {
      list.innerHTML = pats.map(function (p) {
        var conf = Math.round(Number(p.confidence || 0) * 100);
        return '<li><span class="pat-tag ' + biasClass(p.side) + '">' + (p.side || "") +
          '</span> <strong>' + (p.name || "") + '</strong> · ' + conf + '% <span class="muted">' +
          (p.note || "") + "</span></li>";
      }).join("");
    }

    renderFnoTicket("pat-ticket-same", row.call_same_day || (call.kind === "same_day" ? call : {}));
    renderFnoTicket("pat-ticket-next", row.call_next_day || (call.kind === "next_day" ? call : {}));
    renderNormalTicket(row.call_normal || (call.kind === "normal" ? call : {}));

    var posHost = $("pat-pos");
    var adv = row.position_advice || {};
    var opens = adv.open || [];
    if (!opens.length) {
      posHost.innerHTML = '<li class="muted">' + (adv.summary || "No open positions on this index.") + "</li>";
    } else {
      posHost.innerHTML = opens.map(function (p) {
        return '<li><strong>#' + p.position_id + "</strong> " + (p.contract || "") +
          ' → <span class="pat-tag">' + (p.advice || "") + '</span> <span class="muted">' +
          (p.note || "") + "</span></li>";
      }).join("");
    }

    document.querySelectorAll("#pat-tabs button").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-u") === u);
    });
  }

  function applyBoard(payload) {
    board = (payload && payload.board) || (payload && payload.underlyings) || {};
    render(active);
    setModel(payload && payload.model);
    setStatus("Updated " + (payload && payload.ts ? payload.ts : ""));
  }

  function loadBoard() {
    setStatus("Loading…");
    return fetch("/api/patterns/board/", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(applyBoard)
      .catch(function (e) { setStatus("Load failed: " + e); });
  }

  function refreshLive() {
    setBusy(["btn-patterns-refresh", "btn-patterns-backfill", "btn-patterns-train"], true);
    setStatus("Scoring live (INDstocks / Yahoo)…");
    return postJson("/api/patterns/refresh/", {})
      .then(function (res) {
        var payload = res.data || {};
        if (!res.okHttp || payload.ok === false) {
          setStatus("Refresh failed: " + (payload.error || "error"));
          return;
        }
        board = payload.underlyings || board;
        render(active);
        setStatus("Live score " + (payload.ts || "ok"));
      })
      .catch(function (e) { setStatus("Refresh failed: " + e); })
      .finally(function () {
        setBusy(["btn-patterns-refresh", "btn-patterns-backfill", "btn-patterns-train"], false);
      });
  }

  function runBackfill() {
    setBusy(["btn-patterns-refresh", "btn-patterns-backfill", "btn-patterns-train"], true);
    setStatus("Loading 5y daily + 1m/5m for NIFTY & BANKNIFTY… (may take a minute)");
    return postJson("/api/patterns/backfill/", { years: 5, force: true })
      .then(function (res) {
        var d = res.data || {};
        if (!res.okHttp || !d.ok) {
          setStatus("Backfill failed: " + (d.error || "error"));
          return;
        }
        var nifty = (d.detail && d.detail.NIFTY) || {};
        var bank = (d.detail && d.detail.BANKNIFTY) || {};
        setStatus(
          "Data loaded — NIFTY 1d=" + (nifty["1d"] || 0) +
          " 5m=" + (nifty["5m"] || 0) + " 1m=" + (nifty["1m"] || 0) +
          " · BANKNIFTY 1d=" + (bank["1d"] || 0) +
          " 5m=" + (bank["5m"] || 0) + " 1m=" + (bank["1m"] || 0)
        );
        if (window.toast) toast("Patterns data loaded");
      })
      .catch(function (e) { setStatus("Backfill failed: " + e); })
      .finally(function () {
        setBusy(["btn-patterns-refresh", "btn-patterns-backfill", "btn-patterns-train"], false);
      });
  }

  function runTrain() {
    setBusy(["btn-patterns-refresh", "btn-patterns-backfill", "btn-patterns-train"], true);
    setStatus("Training index_pattern_lgbm… (may take a minute)");
    return postJson("/api/patterns/train/", { promote: true })
      .then(function (res) {
        var d = res.data || {};
        if (!res.okHttp || !d.ok) {
          setStatus("Train failed: " + (d.error || "error"));
          return;
        }
        setStatus(
          "Train done — v" + d.version +
          " promoted=" + d.promoted +
          " exp=" + (d.expectancy_R != null ? Number(d.expectancy_R).toFixed(3) : "—") +
          " rows=" + (d.n_rows != null ? d.n_rows : "—")
        );
        setModel({
          name: "index_pattern_lgbm",
          version: d.version,
          expectancy_R: d.expectancy_R,
          auc: d.auc,
          n_rows: d.n_rows,
        });
        if (window.toast) toast(d.promoted ? "Model promoted" : "Candidate saved");
        return loadBoard();
      })
      .catch(function (e) { setStatus("Train failed: " + e); })
      .finally(function () {
        setBusy(["btn-patterns-refresh", "btn-patterns-backfill", "btn-patterns-train"], false);
      });
  }

  document.querySelectorAll("#pat-tabs button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      active = btn.getAttribute("data-u") || "NIFTY";
      history.replaceState(null, "", "/patterns/?u=" + active);
      render(active);
    });
  });

  var refreshBtn = $("btn-patterns-refresh");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshLive);
  var backfillBtn = $("btn-patterns-backfill");
  if (backfillBtn) backfillBtn.addEventListener("click", runBackfill);
  var trainBtn = $("btn-patterns-train");
  if (trainBtn) trainBtn.addEventListener("click", runTrain);

  loadBoard();
  setInterval(loadBoard, 60000);
})();
