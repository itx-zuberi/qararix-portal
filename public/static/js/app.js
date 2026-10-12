// Live clock: starts from the server's time so a wrong laptop/phone clock can't mislead the display.
(function () {
  var el = document.getElementById('clock'); if (!el) return;
  var offset = parseInt(el.dataset.ts, 10) - Date.now(), tz = el.dataset.tz;
  function tick() {
    try { el.textContent = new Date(Date.now() + offset).toLocaleTimeString('en-GB', {timeZone: tz, hour12: false}); } catch (e) {}
  }
  tick(); setInterval(tick, 1000);
})();
