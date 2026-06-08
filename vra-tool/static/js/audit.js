(function () {
  var page = 1;
  var pageSize = 50;
  var total = 0;

  function esc(s) {
    if (!s) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  }

  async function load() {
    var v = document.getElementById("f-vendor").value.trim();
    var f = document.getElementById("f-from").value.trim();
    var t = document.getElementById("f-to").value.trim();
    var qs =
      "?page=" +
      page +
      "&page_size=" +
      pageSize +
      (v ? "&vendor=" + encodeURIComponent(v) : "") +
      (f ? "&date_from=" + encodeURIComponent(f) : "") +
      (t ? "&date_to=" + encodeURIComponent(t) : "");
    var res = await fetch("/api/audit" + qs);
    var data = await res.json();
    total = data.total;
    var body = document.getElementById("audit-body");
    body.innerHTML = "";
    (data.items || []).forEach(function (row) {
      var tr = document.createElement("tr");
      tr.className = "border-b border-slate-100 hover:bg-slate-50";
      var pdfCell = "—";
      if (row.pdf_path) {
        var name = row.pdf_path.replace(/^output\//, "");
        pdfCell =
          '<a class="text-paytm-blue underline" href="/download/pdf/' +
          encodeURIComponent(name) +
          '">Download</a>';
      }
      tr.innerHTML =
        "<td class='p-2 whitespace-nowrap'>" +
        esc(row.timestamp) +
        "</td>" +
        "<td class='p-2'>" +
        esc(row.vendor_name) +
        "</td>" +
        "<td class='p-2'>" +
        esc(row.gst) +
        "</td>" +
        "<td class='p-2'>" +
        esc(row.request_type) +
        "</td>" +
        "<td class='p-2'>" +
        esc(row.status) +
        "</td>" +
        "<td class='p-2'>" +
        esc(row.provider_used) +
        "</td>" +
        "<td class='p-2'>" +
        pdfCell +
        "</td>";
      body.appendChild(tr);
    });
    document.getElementById("audit-meta").textContent =
      "Showing page " + data.page + " — " + data.total + " total rows";
    document.getElementById("btn-prev").disabled = page <= 1;
    document.getElementById("btn-next").disabled = page * pageSize >= total;

    var csv = "/api/audit/export.csv";
    if (v) csv += "?vendor=" + encodeURIComponent(v);
    if (f) csv += (csv.indexOf("?") >= 0 ? "&" : "?") + "date_from=" + encodeURIComponent(f);
    if (t) csv += (csv.indexOf("?") >= 0 ? "&" : "?") + "date_to=" + encodeURIComponent(t);
    document.getElementById("btn-csv").href = csv;
  }

  document.getElementById("btn-filter").addEventListener("click", function () {
    page = 1;
    load();
  });
  document.getElementById("btn-prev").addEventListener("click", function () {
    if (page > 1) {
      page -= 1;
      load();
    }
  });
  document.getElementById("btn-next").addEventListener("click", function () {
    if (page * pageSize < total) {
      page += 1;
      load();
    }
  });

  load();
})();
