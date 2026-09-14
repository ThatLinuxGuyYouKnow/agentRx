/* agentRx map + chat. Flow: drug -> radius/delivery -> candidates
   (map + chat, deselectable) -> consent -> CALL-E calls or email summary.
   Map pattern heisted from cairn MapScreen: Leaflet, divIcon pins, pulse dot. */
(function () {
  'use strict';

  var DEFAULT_LOC = { lat: 40.7128, lng: -74.0060 };
  var userLoc = { lat: DEFAULT_LOC.lat, lng: DEFAULT_LOC.lng };

  // flow state
  var pendingDrug = null;      // {drug, strength, qty, raw}
  var deliveryOn = false;
  var candidates = [];         // pharmacy dicts
  var selected = {};           // pharmacy_id -> true
  var awaitingEmail = false;
  var callInFlight = false; // guard: one call batch at a time (live calls cost money)
  var emailSubject = '';       // drug label for the email
  var lastResults = null;
  var threadId = null;
  var ORG_ID = 'demo-org';

  // persistent thread: resume latest, else create; replay history
  function bootThread() {
    try { threadId = parseInt(localStorage.getItem('agentrx_thread') || '', 10) || null; } catch (e) { threadId = null; }
    function useThread(id) {
      threadId = id;
      try { localStorage.setItem('agentrx_thread', String(id)); } catch (e) {}
      fetch('/api/threads/' + id + '/messages').then(function (r) { return r.json(); }).then(function (msgs) {
        (msgs || []).slice(-20).forEach(function (m) {
          if (m.role === 'user') addUserMessage(m.content);
          else addAgentBubble(esc(m.content));
        });
      }).catch(function () {});
    }
    if (threadId) { useThread(threadId); return; }
    fetch('/api/threads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ org_id: ORG_ID, title: 'coordinator chat' }),
    }).then(function (r) { return r.json(); }).then(function (t) { useThread(t.id); })
      .catch(function () {});
  }

  var map = L.map('map', { zoomControl: false }).setView([userLoc.lat, userLoc.lng], 14);
  L.control.zoom({ position: 'topright' }).addTo(map);
  var TILES = {
    dark: 'https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png',
    light: 'https://basemaps.cartocdn.com/rastertiles/light_all/{z}/{x}/{y}.png',
  };
  var cartoKey = '';
  var theme = 'dark';
  try { theme = localStorage.getItem('agentrx_theme') || 'dark'; } catch (e) { theme = 'dark'; }
  if (theme !== 'light') theme = 'dark';
  function tileUrl(mode) { return TILES[mode] + (cartoKey ? '?key=' + cartoKey : ''); }
  function setTheme(mode) {
    theme = mode;
    document.body.classList.toggle('light', mode === 'light');
    if (typeof tileLayer !== 'undefined') tileLayer.setUrl(tileUrl(mode));
    var t = document.getElementById('theme-toggle');
    if (t) t.textContent = mode === 'light' ? '🌙' : '☀️';
    try { localStorage.setItem('agentrx_theme', mode); } catch (e) {}
  }
  document.body.classList.toggle('light', theme === 'light');

  var tileLayer = L.tileLayer(tileUrl(theme), {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
    maxZoom: 20,
  }).addTo(map);
  document.getElementById('theme-toggle').addEventListener('click', function () {
    setTheme(theme === 'light' ? 'dark' : 'light');
  });
  setTheme(theme); // sync toggle icon with stored theme
  fetch('/api/config').then(function (r) { return r.json(); }).then(function (cfg) {
    if (cfg && cfg.cartoApiKey) {
      cartoKey = cfg.cartoApiKey;
      tileLayer.setUrl(tileUrl(theme));
    }
    if (cfg && (cfg.mock || cfg.calleMode === 'mock')) {
      var badge = document.getElementById('mock-badge');
      if (badge) badge.style.display = '';
    }
  }).catch(function () {});

  var userMarker = L.marker([userLoc.lat, userLoc.lng], {
    icon: L.divIcon({ className: '', html: '<div class="rx-user-dot"></div>', iconSize: [12, 12], iconAnchor: [6, 6] }),
  }).addTo(map);

  var pinLayer = L.layerGroup().addTo(map);
  var routeLine = null;
  var radiusCircle = null;

  function clearOverlays() {
    pinLayer.clearLayers();
    if (routeLine) { map.removeLayer(routeLine); routeLine = null; }
    if (radiusCircle) { map.removeLayer(radiusCircle); radiusCircle = null; }
  }

  function setLoc(lat, lng, label) {
    userLoc = { lat: lat, lng: lng };
    userMarker.setLatLng([lat, lng]);
    document.getElementById('loc-line').textContent = label || (lat.toFixed(4) + ', ' + lng.toFixed(4));
  }

  var locLine = document.getElementById('loc-line');
  locLine.style.pointerEvents = 'auto';
  locLine.style.cursor = 'pointer';
  locLine.title = 'Tap to retry GPS';
  function locateUser() {
    if (!navigator.geolocation) {
      setLoc(DEFAULT_LOC.lat, DEFAULT_LOC.lng, 'NYC · demo (no GPS API — tap to retry)');
      return;
    }
    setLoc(userLoc.lat, userLoc.lng, 'locating…');
    navigator.geolocation.getCurrentPosition(
      function (p) {
        setLoc(p.coords.latitude, p.coords.longitude, 'you · GPS');
        map.flyTo([p.coords.latitude, p.coords.longitude], 14, { duration: 1.2 });
      },
      function (err) {
        var why = err && err.code === 1 ? 'denied' : err && err.code === 3 ? 'timeout' : 'unavailable';
        setLoc(DEFAULT_LOC.lat, DEFAULT_LOC.lng, 'NYC · demo (GPS ' + why + ' — tap to retry)');
      },
      { timeout: 15000, maximumAge: 60000, enableHighAccuracy: false }
    );
  }
  locLine.addEventListener('click', locateUser);
  locateUser();

  function statusOf(r) {
    if (r.in_stock) return 'in';
    if (r.call_status && r.call_status !== 'completed') return 'call';
    return 'out';
  }
  function statusLabel(r) {
    var s = statusOf(r);
    return s === 'in' ? 'In stock' : s === 'call' ? 'Call ahead' : 'Out of stock';
  }

  function popupForCandidate(p) {
    return '<div class="rx-popup"><div class="t">' + esc(p.name) + '</div>' +
      '<div>★' + p.rating + ' (' + p.user_ratings_total + ')' + (p.open_now ? ' · open' : ' · closed') + '</div>' +
      '<div class="m">' + esc(p.address) + '</div>' +
      (p.phone ? '<div class="m">' + esc(p.phone) + '</div>' : '') +
      (p.delivery ? '<div>🛵 delivery likely</div>' : '') + '</div>';
  }

  function drawCandidates(cands, radiusKm) {
    clearOverlays();
    radiusCircle = L.circle([userLoc.lat, userLoc.lng], {
      radius: radiusKm * 1000, color: '#E3A44C', weight: 1.5,
      dashArray: '6 8', opacity: 0.5, fillOpacity: 0.04,
    }).addTo(map);
    var bounds = L.latLngBounds([[userLoc.lat, userLoc.lng]]);
    cands.forEach(function (p) {
      L.marker([p.lat, p.lng], {
        icon: L.divIcon({
          className: '', html: '<div class="rx-pin cand"></div>',
          iconSize: [16, 16], iconAnchor: [8, 8],
        }),
      }).bindPopup(popupForCandidate(p)).addTo(pinLayer);
      bounds.extend([p.lat, p.lng]);
    });
    map.flyToBounds(bounds, { padding: [80, 80], duration: 1.2 });
  }

  function drawResults(results) {
    pinLayer.clearLayers();
    if (routeLine) { map.removeLayer(routeLine); routeLine = null; }
    var bounds = L.latLngBounds([[userLoc.lat, userLoc.lng]]);
    var best = null;
    results.forEach(function (r, i) {
      var s = statusOf(r);
      var isBest = i === 0 && r.in_stock;
      if (isBest) best = r;
      var price = r.price != null ? '$' + r.price.toFixed(2) : '—';
      L.marker([r.pharmacy.lat, r.pharmacy.lng], {
        icon: L.divIcon({
          className: '', html: '<div class="rx-pin ' + s + (isBest ? ' best' : '') + '"></div>',
          iconSize: [16, 16], iconAnchor: [8, 8],
        }),
      }).bindPopup(
        '<div class="rx-popup"><div class="t">' + esc(r.pharmacy.name) + '</div>' +
        '<div>' + statusLabel(r) + ' · ' + price + (r.pickup_time ? ' · ' + esc(r.pickup_time) : '') + '</div>' +
        '<div class="m">' + esc(r.pharmacy.address) + '</div>' +
        (r.transcript_url ? '<div><a href="' + esc(r.transcript_url) + '" target="_blank">transcript ' + esc(r.call_id) + '</a></div>' : '') +
        '</div>'
      ).addTo(pinLayer);
      bounds.extend([r.pharmacy.lat, r.pharmacy.lng]);
    });
    if (best) {
      routeLine = L.polyline(
        [[userLoc.lat, userLoc.lng], [best.pharmacy.lat, best.pharmacy.lng]],
        { color: '#7FA37D', weight: 3, dashArray: '2 10', lineCap: 'round', opacity: 0.85 }
      ).addTo(map);
    }
    map.flyToBounds(bounds, { padding: [80, 80], duration: 1.2 });
  }

  /* ---------- chat ---------- */
  var chatTab = document.getElementById('chat-tab');
  var chatPanel = document.getElementById('chat-panel');
  var collapseBtn = document.getElementById('collapse-btn');
  var chatBody = document.getElementById('chat-body');
  var chatInput = document.getElementById('chat-input');
  var sendBtn = document.getElementById('send-btn');
  var opened = false;

  function openChat() {
    opened = true;
    chatPanel.classList.add('open');
    chatTab.style.display = 'none';
    setTimeout(function () { chatInput.focus(); }, 300);
  }
  function closeChat() {
    opened = false;
    chatPanel.classList.remove('open');
    setTimeout(function () { if (!opened) chatTab.style.display = 'flex'; }, 200);
  }
  chatTab.addEventListener('click', openChat);
  collapseBtn.addEventListener('click', closeChat);

  function scrollToBottom() { chatBody.scrollTop = chatBody.scrollHeight; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function addUserMessage(text) {
    var el = document.createElement('div');
    el.className = 'msg user';
    el.textContent = text;
    chatBody.appendChild(el);
    scrollToBottom();
  }
  function addAgentBubble(html, warn) {
    var wrap = document.createElement('div');
    wrap.className = 'msg agent' + (warn ? ' warn' : '');
    wrap.innerHTML = '<div class="bubble">' + html + '</div>';
    chatBody.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }
  function showTyping() {
    var el = document.createElement('div');
    el.className = 'msg agent';
    el.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
    chatBody.appendChild(el);
    scrollToBottom();
    return el;
  }

  // "atorvastatin 20mg x30" -> {drug, strength, qty}
  function parseQuery(q) {
    var qty = 30, strength = '', drug = q.trim();
    var m = drug.match(/\s*x\s*(\d+)\s*(tabs?|tablets?)?\s*$/i);
    if (m) { qty = parseInt(m[1], 10); drug = drug.slice(0, m.index).trim(); }
    m = drug.match(/(\d+\s?(?:mg|g|mcg|ml|%|iu))\b/i);
    if (m) {
      strength = m[1].replace(/\s+/, '');
      drug = (drug.slice(0, m.index) + ' ' + drug.slice(m.index + m[0].length)).trim().replace(/\s+/g, ' ');
    }
    return { drug: drug, strength: strength, qty: qty };
  }

  function resetFlow() {
    candidates = []; selected = {};
    awaitingEmail = false; lastResults = null;
    clearOverlays();
  }

  function askRadius(p) {
    pendingDrug = p;
    deliveryOn = false;
    addAgentBubble(
      'Got it: <strong>' + esc(p.raw) + '</strong>. How far should I look, and do you need delivery?' +
      '<div class="chips" style="margin-top:10px">' +
      '<button class="chip" data-radius="1">1 km</button>' +
      '<button class="chip" data-radius="2">2 km</button>' +
      '<button class="chip" data-radius="5">5 km</button>' +
      '<button class="chip" data-radius="10">10 km</button>' +
      '<button class="chip" data-act="toggle-delivery">🛵 delivery: off</button>' +
      '</div>'
    );
  }

  function fetchCandidates(radiusKm) {
    var typing = showTyping();
    fetch('/api/pharmacies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: userLoc.lat, lng: userLoc.lng, radius_km: radiusKm, delivery: deliveryOn }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        typing.remove();
        candidates = data.pharmacies || [];
        selected = {};
        candidates.forEach(function (p) { selected[p.pharmacy_id] = true; });
        if (!candidates.length) {
          addAgentBubble('No reputable pharmacies found within ' + radiusKm + ' km. Try a wider radius — or ask your pharmacist directly.', true);
          return;
        }
        drawCandidates(candidates, radiusKm);
        renderCandidateBubble(radiusKm, data.disclaimer);
      })
      .catch(function () {
        typing.remove();
        addAgentBubble('Search failed — check the server and try again.', true);
      });
  }

  function selectedList() {
    return candidates.filter(function (p) { return selected[p.pharmacy_id]; });
  }

  function renderCandidateBubble(radiusKm, disclaimer) {
    var rows = candidates.map(function (p) {
      var on = !!selected[p.pharmacy_id];
      var sub = '★' + p.rating + ' (' + p.user_ratings_total + ')' + (p.open_now ? ' · open' : '') +
        ' · ' + p.distance_km + ' km' + (p.delivery ? ' · 🛵 delivery likely' : '');
      return '<div class="result cand-row' + (on ? '' : ' deselected') + '" data-act="toggle-ph" data-id="' + esc(p.pharmacy_id) + '">' +
        '<div class="r1"><div class="left"><span class="tick' + (on ? '' : ' off') + '"></span>' +
        '<span class="name">' + esc(p.name) + '</span></div></div>' +
        '<div class="meta">' + sub + '</div></div>';
    }).join('');
    addAgentBubble(
      'Found <strong>' + candidates.length + '</strong> within ' + radiusKm + ' km' +
      (deliveryOn ? ' (delivery)' : '') + '. Tap to deselect any, then approve — <em>no calls placed yet</em>.' +
      '<div class="result-list">' + rows + '</div>' +
      '<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">' +
      '<button class="mini-btn" data-act="go-call">📞 Call <span class="call-count">' + selectedList().length + '</span> selected</button>' +
      '<button class="mini-btn" data-act="go-email">✉️ Email me this list</button></div>' +
      '<div class="disclaimer" style="margin-top:8px">' + esc(disclaimer || '') + '</div>'
    ).dataset.candBubble = '1';
  }

  function refreshCandidateBubble(bubble) {
    var list = selectedList();
    bubble.querySelectorAll('.cand-row').forEach(function (row) {
      var on = !!selected[row.dataset.id];
      row.classList.toggle('deselected', !on);
      row.querySelector('.tick').classList.toggle('off', !on);
    });
    var btn = bubble.querySelector('.call-count');
    if (btn) btn.textContent = list.length;
  }

  function goCall() {
    if (callInFlight) return; // ignore double-taps while a batch is in flight
    var list = selectedList();
    if (!list.length) { addAgentBubble('Select at least one pharmacy first.', true); return; }
    callInFlight = true;
    addUserMessage('✓ Approved — calling ' + list.length + ' pharmac' + (list.length > 1 ? 'ies' : 'y'));
    var typing = showTyping();
    fetch('/api/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        drug: pendingDrug.drug, strength: pendingDrug.strength, qty: pendingDrug.qty,
        lat: userLoc.lat, lng: userLoc.lng, pharmacies: list,
        watch_id: pendingDrug.watchId || null,
      }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        typing.remove();
        callInFlight = false;
        lastResults = data.results || [];
        addAgentBubble(renderResults(pendingDrug.raw, data), data.needs_human);
        drawResults(lastResults);
      })
      .catch(function () {
        typing.remove();
        callInFlight = false;
        addAgentBubble('Calls failed — check the server and try again.', true);
      });
  }

  function priceOf(r) { return r.price != null ? '$' + r.price.toFixed(2) : null; }

  // Coordinator-style summary: verdict first, then the follow-up question.
  function summaryLine(drugLabel, data) {
    var results = data.results || [];
    var inStock = results.filter(function (r) { return r.in_stock; });
    if (!results.length) {
      return 'I couldn\'t reach any pharmacy for <strong>' + esc(drugLabel) + '</strong>. ' +
        'Try a wider radius — or call your pharmacist directly.';
    }
    if (inStock.length) {
      var best = inStock[0]; // cheapest in-stock first (server-sorted)
      var detail = priceOf(best) || 'price unknown';
      if (best.pickup_time) detail += ', ready ' + esc(best.pickup_time);
      var others = inStock.slice(1).map(function (r) {
        var bits = priceOf(r) || 'price unknown';
        if (r.pickup_time) bits += ' · ' + esc(r.pickup_time);
        return esc(r.pharmacy.name) + ' (' + bits + ')';
      }).join(', ');
      var out = inStock.length > 1
        ? 'Good news — <strong>' + inStock.length + ' pharmacies</strong> have <strong>' + esc(drugLabel) + '</strong> in stock. '
        : 'Good news! ';
      out += '<strong>' + esc(best.pharmacy.name) + '</strong> has <strong>' + esc(drugLabel) + '</strong> in stock at ' + detail + '.';
      if (others) out += '<div class="meta">Also in stock: ' + others + '.</div>';
      if (inStock.length > 1) {
        var closest = inStock.slice().sort(function (a, b) {
          return a.pharmacy.distance_km - b.pharmacy.distance_km;
        })[0];
        var cmp = 'Cheapest: ' + esc(best.pharmacy.name) + (priceOf(best) ? ' (' + priceOf(best) + ')' : '');
        if (closest.pharmacy.name !== best.pharmacy.name) {
          cmp += ' · Closest: ' + esc(closest.pharmacy.name) + ' (' + closest.pharmacy.distance_km + ' km)';
        }
        var deliv = inStock.filter(function (r) { return r.pharmacy.delivery; }).map(function (r) {
          return esc(r.pharmacy.name);
        });
        if (deliv.length) cmp += ' · 🛵 likely delivery: ' + deliv.join(', ');
        out += '<div class="meta">⚖️ ' + cmp + '.</div>';
      }
      var oos = results.length - inStock.length;
      if (oos) out += '<div class="meta">Out of stock at ' + oos + ' other' + (oos > 1 ? 's' : '') + '.</div>';
      out += followUp(best, drugLabel);
      return out;
    }
    var head = results.length > 1
      ? 'None of the ' + results.length + ' pharmacies checked have'
      : 'The pharmacy checked doesn\'t have';
    return head + ' <strong>' + esc(drugLabel) + '</strong> right now. ' +
      'I\'d keep the refill reminder set and watch this drug — I\'ll flag it on the shortage board when it comes back.';
  }

  function followUp(best, drugLabel) {
    var phone = best && best.pharmacy && best.pharmacy.phone;
    var out = '<div style="margin-top:8px">Want to follow up? ';
    if (phone) {
      out += 'Call them directly at <a href="tel:' + esc(phone.replace(/\s/g, '')) + '">' + esc(phone) + '</a> to confirm and arrange pickup';
      if (best.pharmacy.delivery) out += ' — 🛵 delivery looks likely there, ask about it when you call';
      out += '. ';
    } else {
      out += 'Call the pharmacy to confirm and arrange pickup. ';
    }
    out += 'Or tap below and I\'ll set a reminder, email you this list, or watch <strong>' + esc(drugLabel) + '</strong> for you.</div>';
    return out;
  }

  function renderResults(drugLabel, data) {
    var rows = data.results.map(function (r) {
      var s = statusOf(r);
      var price = priceOf(r) || '—';
      var sub = statusLabel(r) + ' · ' + price + (r.pickup_time ? ' · ' + esc(r.pickup_time) : '');
      var tx = r.call_id
        ? '<div class="r2"><button class="mini-btn" data-act="transcript" data-call-id="' + esc(r.call_id) + '" title="' + esc(r.call_id) + '">transcript</button></div>' +
          '<div class="tx" style="display:none"></div>'
        : '';
      return '<div class="result"><div class="r1"><div class="left">' +
        '<span class="status-dot ' + s + '"></span><span class="name">' + esc(r.pharmacy.name) + '</span></div>' +
        '<span class="dist">' + r.pharmacy.distance_km + ' km</span></div>' +
        '<div class="meta">' + sub + '</div>' + tx +
        '</div>';
    }).join('');
    var warn = data.needs_human
      ? '<div style="margin-top:8px">Handoff: ' + esc(data.handoff_reason) + ' — please call your pharmacist directly.</div>'
      : '';
    return 'Called ' + data.results.length + ' pharmac' + (data.results.length > 1 ? 'ies' : 'y') +
      ' for <strong>' + esc(drugLabel) + '</strong>.' +
      '<div style="margin-top:8px">' + summaryLine(drugLabel, data) + '</div>' +
      '<div class="result-list">' + rows + '</div>' + warn +
      '<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">' +
      '<button class="mini-btn" data-act="remind">Set refill reminder</button>' +
      '<button class="mini-btn" data-act="watch-drug">👁 Watch this drug</button>' +
      '<button class="mini-btn" data-act="go-email">✉️ Email me results</button></div>' +
      '<div class="disclaimer" style="margin-top:8px">' + esc(data.disclaimer || '') + '</div>';
  }

  function summaryText() {
    if (lastResults) {
      return lastResults.map(function (r) {
        return '- ' + r.pharmacy.name + ': ' + statusLabel(r) +
          (r.price != null ? ', $' + r.price.toFixed(2) : '') +
          (r.pickup_time ? ', ' + r.pickup_time : '');
      }).join('\n');
    }
    return selectedList().map(function (p) {
      return '- ' + p.name + ' (' + p.address + ')' + (p.phone ? ' ' + p.phone : '');
    }).join('\n');
  }

  function askEmail() {
    awaitingEmail = true;
    emailSubject = pendingDrug ? pendingDrug.raw : 'pharmacy list';
    addAgentBubble('Which email should I send it to?');
  }

  function sendEmail(addr) {
    awaitingEmail = false;
    var typing = showTyping();
    fetch('/api/email-summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_email: addr, drug: emailSubject, summary: summaryText() }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error('email-unconfigured');
        return res.json();
      })
      .then(function () {
        typing.remove();
        addAgentBubble('Sent to <strong>' + esc(addr) + '</strong>.');
      })
      .catch(function () {
        typing.remove();
        addAgentBubble('Email isn’t configured on the server (needs SMTP_* env) — the list above is yours to copy.', true);
      });
  }

  function showReminders() {
    var typing = showTyping();
    fetch('/api/reminders')
      .then(function (r) { return r.json(); })
      .then(function (list) {
        typing.remove();
        if (!list.length) { addAgentBubble('No reminders yet. Search a drug first, then tap “Set refill reminder”.'); return; }
        var rows = list.map(function (r) {
          return '<div class="result"><div class="r1"><div class="left"><span class="name">' +
            esc(r.drug) + '</span></div><span class="dist">' + esc(r.remind_date) + '</span></div>' +
            '<div class="r2"><button class="mini-btn danger" data-act="del-rem" data-id="' + r.id + '">delete</button></div></div>';
        }).join('');
        addAgentBubble('Your refill reminders:<div class="result-list">' + rows + '</div>');
      })
      .catch(function () { typing.remove(); addAgentBubble('Could not load reminders.', true); });
  }

  function handleQuery(raw) {
    if (!raw || !raw.trim()) return;
    if (raw === '__reminders') { showReminders(); return; }
    if (awaitingEmail) {
      var addr = raw.trim();
      addUserMessage(addr);
      chatInput.value = '';
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(addr)) {
        addAgentBubble('That doesn’t look like an email — try again?', true);
        return;
      }
      sendEmail(addr);
      return;
    }
    var text = raw.trim();
    var low = text.toLowerCase();
    if (/^(watch|unwatch|stop watching)\b/.test(low) || low === 'board' || low.indexOf('shortage') !== -1) {
      if (low === 'board' || low.indexOf('shortage') !== -1) { showBoard(); chatInput.value = ''; return; }
      sendToThread(text);
      return;
    }
    var p = parseQuery(text);
    if (!p.drug) { sendToThread(text); return; }
    resetFlow();
    p.raw = text;
    addUserMessage(text);
    chatInput.value = '';
    askRadius(p);
  }

  function sendToThread(text) {
    addUserMessage(text);
    chatInput.value = '';
    if (!threadId) { addAgentBubble('Starting a fresh thread — one moment…', true); bootThread(); return; }
    var typing = showTyping();
    fetch('/api/threads/' + threadId + '/messages', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text, org_id: ORG_ID, lat: userLoc.lat, lng: userLoc.lng }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        typing.remove();
        if (data.reply) addAgentBubble(esc(data.reply));
        else addAgentBubble('The agent stumbled — try again.', true);
      })
      .catch(function () { typing.remove(); addAgentBubble('Agent unreachable — is Bedrock configured?', true); });
  }

  function showBoard() {
    addUserMessage('board');
    var typing = showTyping();
    fetch('/api/board?org_id=' + encodeURIComponent(ORG_ID))
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        typing.remove();
        if (!rows.length) {
          addAgentBubble('Watchlist is empty. Say “watch lisinopril 10mg” and I’ll track it.');
          return;
        }
        var html = rows.map(function (row) {
          var w = row.watch;
          var label = esc(w.drug + (w.strength ? ' ' + w.strength : ''));
          var head = row.stale
            ? '<span class="meta" style="color:#b3261e">stale — re-check?</span>'
            : '<span class="meta">checked ' + esc((row.snapshot.checked_at || '').slice(0, 10)) + '</span>';
          var cells = '';
          if (row.snapshot && row.snapshot.result && row.snapshot.result.results) {
            cells = row.snapshot.result.results.map(function (r) {
              var s = r.in_stock ? 'in' : 'out';
              var price = r.price != null ? ' $' + r.price.toFixed(2) : '';
              return '<div class="meta"><span class="status-dot ' + s + '" style="display:inline-block;margin-right:6px"></span>' +
                esc(r.pharmacy.name) + price + '</div>';
            }).join('');
          } else {
            cells = '<div class="meta">no checks yet</div>';
          }
          return '<div class="result"><div class="r1"><div class="left"><span class="name">' + label + '</span></div>' + head + '</div>' +
            cells +
            '<div class="r2"><button class="mini-btn" data-act="recheck" data-watch-id="' + w.id + '">re-check</button></div></div>';
        }).join('');
        addAgentBubble('Shortage board:<div class="result-list">' + html + '</div>');
      })
      .catch(function () { typing.remove(); addAgentBubble('Could not load the board.', true); });
  }

  chatBody.addEventListener('click', function (e) {
    var btn = e.target.closest('button, .cand-row');
    if (!btn) return;
    if (btn.dataset.drug) { handleQuery(btn.dataset.drug); return; }
    if (btn.dataset.radius) {
      addUserMessage('within ' + btn.dataset.radius + ' km' + (deliveryOn ? ' + delivery' : ''));
      fetchCandidates(parseFloat(btn.dataset.radius));
      return;
    }
    if (btn.dataset.act === 'toggle-delivery') {
      deliveryOn = !deliveryOn;
      btn.textContent = '🛵 delivery: ' + (deliveryOn ? 'on' : 'off');
      btn.classList.toggle('chip-on', deliveryOn);
      return;
    }
    if (btn.dataset.act === 'toggle-ph') {
      var id = btn.dataset.id;
      selected[id] = !selected[id];
      var bubble = btn.closest('.msg');
      if (bubble) refreshCandidateBubble(bubble);
      return;
    }
    if (btn.dataset.act === 'go-call') { goCall(); return; }
    if (btn.dataset.act === 'go-email') { askEmail(); return; }
    if (btn.dataset.act === 'recheck') {
      var wid = parseInt(btn.dataset.watchId, 10);
      fetch('/api/watchlist?org_id=' + encodeURIComponent(ORG_ID))
        .then(function (r) { return r.json(); })
        .then(function (watches) {
          var w = (watches || []).filter(function (x) { return x.id === wid; })[0];
          if (!w) return;
          resetFlow();
          pendingDrug = {
            drug: w.drug, strength: w.strength || '', qty: w.qty || 30,
            raw: (w.drug + ' ' + (w.strength || '')).trim(), watchId: w.id,
          };
          deliveryOn = !!w.delivery;
          addUserMessage('re-check ' + pendingDrug.raw);
          askRadius(pendingDrug);
        });
      return;
    }
    if (btn.dataset.act === 'remind' && pendingDrug) {
      var today = new Date().toISOString().slice(0, 10);
      fetch('/api/reminders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 'demo-user', drug: pendingDrug.drug, days_supply: 30, last_date: today }),
      })
        .then(function (r) { return r.json(); })
        .then(function (rem) {
          addAgentBubble('Reminder set: <strong>' + esc(rem.drug) + '</strong>, refill by <strong>' + esc(rem.remind_date) + '</strong>.');
        });
      return;
    }
    if (btn.dataset.act === 'del-rem') {
      fetch('/api/reminders/' + btn.dataset.id, { method: 'DELETE' }).then(function () {
        btn.closest('.result').remove();
      });
      return;
    }
    if (btn.dataset.act === 'watch-drug' && pendingDrug) {
      fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          org_id: ORG_ID, drug: pendingDrug.drug,
          strength: pendingDrug.strength || '', qty: pendingDrug.qty || 30,
        }),
      })
        .then(function (r) { return r.json(); })
        .then(function () {
          addAgentBubble('Watching <strong>' + esc(pendingDrug.raw) + '</strong> — find it on the 📋 board, and re-check anytime from there.');
        })
        .catch(function () { addAgentBubble('Couldn\'t add the watch — try again.', true); });
      return;
    }
    if (btn.dataset.act === 'transcript') {
      var box = btn.closest('.result').querySelector('.tx');
      if (!box) return;
      if (box.dataset.loaded) {
        box.style.display = box.style.display === 'none' ? '' : 'none';
        return;
      }
      box.textContent = 'loading…';
      box.style.display = '';
      fetch('/api/calls/' + encodeURIComponent(btn.dataset.callId) + '/transcript')
        .then(function (res) {
          return res.json().then(function (j) { return { ok: res.ok, j: j }; });
        })
        .then(function (x) {
          box.dataset.loaded = '1';
          if (x.ok && x.j && x.j.transcript) {
            box.innerHTML = String(x.j.transcript).split('\n').map(function (line) {
              var m = line.match(/^(Agent|Pharmacy):\s?(.*)$/);
              var who = m ? m[1] : '', what = m ? m[2] : line;
              return '<div class="tx-turn"><span class="tx-who">' + esc(who) +
                (who ? ': ' : '') + '</span>' + esc(what) + '</div>';
            }).join('');
          } else {
            box.textContent = 'Transcript unavailable.';
          }
        })
        .catch(function () { box.textContent = 'Transcript unavailable.'; });
      return;
    }
  });

  sendBtn.addEventListener('click', function () { handleQuery(chatInput.value); });
  chatInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') handleQuery(chatInput.value);
  });

  /* ---------- board drawer ---------- */
  var boardPanel = document.getElementById('board-panel');
  var boardToggle = document.getElementById('board-toggle');
  var boardList = document.getElementById('board-list');
  document.getElementById('board-close').addEventListener('click', function () {
    boardPanel.classList.remove('open');
    boardToggle.classList.remove('on');
  });
  boardToggle.addEventListener('click', function () {
    var open = boardPanel.classList.toggle('open');
    boardToggle.classList.toggle('on', open);
    if (open) loadBoardPanel();
  });

  function loadBoardPanel() {
    boardList.innerHTML = '<div class="meta" style="font-family:IBM Plex Mono,monospace;font-size:12px;color:#78705F">loading…</div>';
    fetch('/api/board?org_id=' + encodeURIComponent(ORG_ID))
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        if (!rows.length) {
          boardList.innerHTML = '<div class="meta" style="font-family:IBM Plex Mono,monospace;font-size:12px;color:#78705F">Nothing watched yet — add a drug below or say “watch …” in chat.</div>';
          return;
        }
        boardList.innerHTML = rows.map(function (row) {
          var w = row.watch;
          var label = esc(w.drug + (w.strength ? ' ' + w.strength : ''));
          var badge = row.stale
            ? '<span class="stale-badge stale">stale</span>'
            : '<span class="stale-badge fresh">' + esc((row.snapshot.checked_at || '').slice(0, 10)) + '</span>';
          var cells = '';
          if (row.snapshot && row.snapshot.result && row.snapshot.result.results) {
            cells = row.snapshot.result.results.map(function (r) {
              var s = r.in_stock ? 'in' : 'out';
              var price = r.price != null ? ' $' + r.price.toFixed(2) : '';
              return '<div class="meta" style="font-family:IBM Plex Mono,monospace;font-size:11.5px;color:#78705F">' +
                '<span class="status-dot ' + s + '" style="display:inline-block;margin-right:6px"></span>' +
                esc(r.pharmacy.name) + price + '</div>';
            }).join('');
          } else {
            cells = '<div class="meta" style="font-family:IBM Plex Mono,monospace;font-size:11.5px;color:#78705F">no checks yet</div>';
          }
          return '<div class="board-row"><div class="r1"><span class="name">' + label + '</span>' + badge + '</div>' +
            '<div class="board-cells">' + cells + '</div>' +
            '<div class="actions"><button class="mini-btn" data-act="recheck" data-watch-id="' + w.id + '">re-check</button>' +
            '<button class="mini-btn danger" data-act="unwatch" data-watch-id="' + w.id + '">unwatch</button></div></div>';
        }).join('');
      })
      .catch(function () { boardList.innerHTML = 'Could not load board.'; });
  }

  boardList.addEventListener('click', function (e) {
    var btn = e.target.closest('button');
    if (!btn) return;
    var wid = parseInt(btn.dataset.watchId, 10);
    if (btn.dataset.act === 'unwatch') {
      fetch('/api/watchlist/' + wid + '?org_id=' + encodeURIComponent(ORG_ID), { method: 'DELETE' })
        .then(loadBoardPanel);
      return;
    }
    if (btn.dataset.act === 'recheck') {
      fetch('/api/watchlist?org_id=' + encodeURIComponent(ORG_ID))
        .then(function (r) { return r.json(); })
        .then(function (watches) {
          var w = (watches || []).filter(function (x) { return x.id === wid; })[0];
          if (!w) return;
          boardPanel.classList.remove('open');
          boardToggle.classList.remove('on');
          if (!chatPanel.classList.contains('open')) openChat();
          resetFlow();
          pendingDrug = {
            drug: w.drug, strength: w.strength || '', qty: w.qty || 30,
            raw: (w.drug + ' ' + (w.strength || '')).trim(), watchId: w.id,
          };
          deliveryOn = !!w.delivery;
          askRadius(pendingDrug);
        });
    }
  });

  document.getElementById('watch-add-btn').addEventListener('click', function () {
    var drug = document.getElementById('watch-drug').value.trim();
    var strength = document.getElementById('watch-strength').value.trim();
    if (!drug) return;
    fetch('/api/watchlist', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ org_id: ORG_ID, drug: drug, strength: strength }),
    }).then(function () {
      document.getElementById('watch-drug').value = '';
      document.getElementById('watch-strength').value = '';
      loadBoardPanel();
    });
  });

  bootThread();
  openChat(); // visible concept on load
})();
