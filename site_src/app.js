(function () {
  var btn = document.getElementById("themebtn");
  if (btn) btn.addEventListener("click", function () {
    var r = document.documentElement, cur = r.getAttribute("data-theme");
    var dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    var next = dark ? "light" : "dark";
    r.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) {}
  });

  var tbody = document.getElementById("rows");
  if (!tbody) return;
  var PAGE = 100, shown = 0, matches = [], data = null;
  var q = document.getElementById("q"), fs = document.getElementById("fs"),
      fr = document.getElementById("fr"), fsrc = document.getElementById("fsrc"),
      count = document.getElementById("count"), more = document.getElementById("more");

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function row(d) {
    var rel = d[0] ? '<span class="pill ' + d[0] + '">' + d[0] + "</span>" : '<span class="chip">unrated</span>';
    var ss = d[2].map(function (i) { return data.searches[i].id; }).join(", ");
    return "<tr><td>" + rel + '</td><td><a href="' + esc(d[5]) + '" rel="noopener">' + esc(d[1]) + '</a></td><td class="m">' +
      esc(ss) + '</td><td class="m">' + esc(d[3]) + '</td><td class="m">' + esc(d[4]) + '</td><td class="m"><a href="digests/' + esc(d[6]) + '.html">' + esc(d[6]) + "</a></td></tr>";
  }
  function draw(reset) {
    if (reset) { tbody.innerHTML = ""; shown = 0; }
    var slice = matches.slice(shown, shown + PAGE);
    tbody.insertAdjacentHTML("beforeend", slice.map(row).join(""));
    shown += slice.length;
    if (!matches.length) tbody.innerHTML = '<tr><td colspan="6" style="color:var(--muted)">No items match these filters.</td></tr>';
    count.textContent = "Showing " + shown.toLocaleString() + " of " + matches.length.toLocaleString() + " matching items (" + data.rows.length.toLocaleString() + " total)";
    more.hidden = shown >= matches.length;
  }
  function filter() {
    var text = q.value.trim().toLowerCase(), s = fs.value, r = fr.value, c = fsrc.value;
    matches = data.rows.filter(function (d) {
      if (r && d[0] !== r) return false;
      if (c && d[3] !== c) return false;
      if (s && !d[2].some(function (i) { return data.searches[i].id === s; })) return false;
      return !text || d[1].toLowerCase().indexOf(text) > -1 || d[5].toLowerCase().indexOf(text) > -1;
    });
    draw(true);
  }
  fetch("items.json").then(function (r) { return r.json(); }).then(function (j) {
    data = j;
    data.searches.forEach(function (s) { fs.insertAdjacentHTML("beforeend", '<option value="' + esc(s.id) + '">' + esc(s.id) + "</option>"); });
    var srcs = {}; data.rows.forEach(function (d) { srcs[d[3]] = 1; });
    Object.keys(srcs).sort().forEach(function (s) { fsrc.insertAdjacentHTML("beforeend", "<option>" + esc(s) + "</option>"); });
    [q, fs, fr, fsrc].forEach(function (el) { el.addEventListener("input", filter); });
    more.addEventListener("click", function () { draw(false); });
    filter();
  }).catch(function () { count.textContent = "Could not load items.json."; });
})();
