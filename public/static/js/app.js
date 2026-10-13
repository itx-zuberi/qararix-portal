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
