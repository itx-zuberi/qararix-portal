// Live clock: starts from the server's time so a wrong laptop/phone clock can't mislead the display.
(function () {
  var el = document.getElementById('clock'); if (!el) return;
  var offset = parseInt(el.dataset.ts, 10) - Date.now(), tz = el.dataset.tz;
  function tick() {
    try { el.textContent = new Date(Date.now() + offset).toLocaleTimeString('en-GB', {timeZone: tz, hour12: false}); } catch (e) {}
  }
  tick(); setInterval(tick, 1000);
})();

// Mobile: label every table cell with its column heading so tables can turn into readable cards (CSS does the rest).
(function () {
  var tables = document.querySelectorAll('table');
  for (var t = 0; t < tables.length; t++) {
    var tb = tables[t], heads = [], ths = tb.querySelectorAll('thead th');
    for (var h = 0; h < ths.length; h++) heads.push(ths[h].textContent.trim());
    tb.classList.add('stack');
    var rows = tb.querySelectorAll('tbody tr');
    for (var r = 0; r < rows.length; r++) {
      var cells = rows[r].children;
      for (var c = 0; c < cells.length; c++) {
        if (cells[c].colSpan > 1 || !heads[c]) continue;
        cells[c].setAttribute('data-label', heads[c]);
      }
    }
  }
})();

// Confirm dialogs for forms that carry data-confirm (e.g. permanent delete)
document.addEventListener('submit', function (ev) {
  var msg = ev.target.getAttribute && ev.target.getAttribute('data-confirm');
  if (msg && !window.confirm(msg)) ev.preventDefault();
});

// "Vacant ID" chips: tap to fill the Employee ID field
document.addEventListener('click', function (ev) {
  var b = ev.target.closest && ev.target.closest('[data-fill]');
  if (!b) return;
  var inp = document.querySelector('input[name=emp_id]');
  if (inp) { inp.value = b.getAttribute('data-fill'); inp.focus(); }
});

// Profile photo: shrink big camera photos in the browser before upload (faster upload, faster avatars, no size errors)
(function () {
  var inp = document.querySelector('input[type=file][name=photo]');
  if (!inp || !window.DataTransfer || !window.HTMLCanvasElement) return;
  inp.addEventListener('change', function () {
    var f = inp.files && inp.files[0];
    if (!f || !/^image\/(jpeg|png|webp)$/.test(f.type)) return;
    var url = URL.createObjectURL(f), img = new Image();
    img.onload = function () {
      var s = Math.min(1, 480 / Math.max(img.width, img.height)), w = Math.round(img.width * s), h = Math.round(img.height * s);
      var cv = document.createElement('canvas'); cv.width = w; cv.height = h;
      var cx = cv.getContext('2d'); cx.fillStyle = '#fff'; cx.fillRect(0, 0, w, h); cx.drawImage(img, 0, 0, w, h);
      cv.toBlob(function (b) {
        URL.revokeObjectURL(url);
        if (!b) return;
        var dt = new DataTransfer(); dt.items.add(new File([b], 'photo.jpg', {type: 'image/jpeg'})); inp.files = dt.files;
      }, 'image/jpeg', 0.86);
    };
    img.onerror = function () { URL.revokeObjectURL(url); };
    img.src = url;
  });
})();
