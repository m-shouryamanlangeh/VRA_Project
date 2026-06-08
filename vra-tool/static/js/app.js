/**
 * Home: single VRA generation + batch Excel upload.
 */
(function () {
  function showToast(message, ok) {
    var el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.style.background = ok ? "#002970" : "#b91c1c";
    el.classList.remove("hidden");
    setTimeout(function () {
      el.classList.add("hidden");
    }, 6000);
  }

  var tabGen = document.getElementById("tab-generate");
  var tabBatch = document.getElementById("tab-batch");
  var panelGen = document.getElementById("panel-generate");
  var panelBatch = document.getElementById("batch-panel");

  function showPanel(which) {
    if (!panelGen || !panelBatch) return;
    if (which === "batch") {
      panelGen.classList.add("hidden");
      panelBatch.classList.remove("hidden");
      tabGen.classList.remove("active");
      tabBatch.classList.add("active");
    } else {
      panelBatch.classList.add("hidden");
      panelGen.classList.remove("hidden");
      tabBatch.classList.remove("active");
      tabGen.classList.add("active");
    }
  }

  if (tabGen)
    tabGen.addEventListener("click", function () {
      showPanel("gen");
    });
  if (tabBatch)
    tabBatch.addEventListener("click", function () {
      showPanel("batch");
    });

  if (window.location.hash === "#batch-panel") showPanel("batch");

  var form = document.getElementById("vra-form");
  var progressWrap = document.getElementById("progress-wrap");
  var progressText = document.getElementById("progress-text");
  var progressSteps = document.getElementById("progress-steps");
  var btnGen = document.getElementById("btn-generate");

  function setProgress(msg, step) {
    if (progressText) progressText.textContent = msg;
    if (progressSteps && step) {
      var li = document.createElement("li");
      li.textContent = "✓ " + step;
      progressSteps.appendChild(li);
    }
  }

  if (form) {
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      if (progressSteps) progressSteps.innerHTML = "";
      if (progressWrap) progressWrap.classList.remove("hidden");
      if (btnGen) btnGen.disabled = true;
      var fd = new FormData(form);
      var body = {
        vendor_name: fd.get("vendor_name"),
        gst: (fd.get("gst") || "").toString().toUpperCase(),
        org_type: fd.get("org_type"),
      };
      try {
        setProgress("Searching open sources…", null);
        await new Promise(function (r) {
          setTimeout(r, 400);
        });
        setProgress("Gathering OSINT evidence…", "Searching open sources…");
        await new Promise(function (r) {
          setTimeout(r, 400);
        });
        setProgress("Validating findings…", "Gathering OSINT evidence…");
        var res = await fetch("/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        var data = await res.json().catch(function () {
          return {};
        });
        if (!res.ok) {
          throw new Error(data.detail || res.statusText || "Generation failed");
        }
        setProgress("Generating PDF…", "Validating findings…");
        setProgress("Done", "Generating PDF…");
        var q =
          "?pdf=" +
          encodeURIComponent(data.pdf_url || "") +
          "&vendor=" +
          encodeURIComponent(body.vendor_name) +
          "&audit_id=" +
          encodeURIComponent(String(data.audit_id || ""));
        window.location.href = "/result" + q;
      } catch (err) {
        showToast(err.message || String(err), false);
        if (progressWrap) progressWrap.classList.add("hidden");
      } finally {
        if (btnGen) btnGen.disabled = false;
      }
    });
  }

  /* Batch */
  var drop = document.getElementById("drop-zone");
  var fileInput = document.getElementById("batch-file");
  var previewWrap = document.getElementById("batch-preview-wrap");
  var previewTable = document.getElementById("batch-preview");
  var btnBatch = document.getElementById("btn-batch-run");
  var batchProgress = document.getElementById("batch-progress");
  var batchBar = document.getElementById("batch-bar");
  var batchStatus = document.getElementById("batch-status");
  var batchDl = document.getElementById("batch-download");
  var batchFile = null;

  function pickFile(f) {
    if (!f) return;
    batchFile = f;
    if (btnBatch) btnBatch.disabled = false;
    if (typeof XLSX === "undefined") {
      showToast("Excel preview unavailable (library load error)", false);
      return;
    }
    var reader = new FileReader();
    reader.onload = function (ev) {
      try {
        var wb = XLSX.read(ev.target.result, { type: "binary" });
        var sheet = wb.Sheets[wb.SheetNames[0]];
        var rows = XLSX.utils.sheet_to_json(sheet, { header: 1 });
        var head = rows[0] || [];
        var body = rows.slice(1, 6);
        if (previewTable) {
          previewTable.innerHTML = "";
          var hr = document.createElement("tr");
          head.forEach(function (h) {
            var th = document.createElement("th");
            th.className = "text-left p-2 border border-slate-200 bg-slate-50";
            th.textContent = h == null ? "" : String(h);
            hr.appendChild(th);
          });
          previewTable.appendChild(hr);
          body.forEach(function (row) {
            var tr = document.createElement("tr");
            head.forEach(function (_, i) {
              var td = document.createElement("td");
              td.className = "p-2 border border-slate-100";
              td.textContent = row[i] == null ? "" : String(row[i]);
              tr.appendChild(td);
            });
            previewTable.appendChild(tr);
          });
        }
        if (previewWrap) previewWrap.classList.remove("hidden");
      } catch (e) {
        showToast("Could not read Excel: " + e.message, false);
      }
    };
    reader.readAsBinaryString(f);
  }

  if (drop && fileInput) {
    drop.addEventListener("click", function () {
      fileInput.click();
    });
    drop.addEventListener("dragover", function (e) {
      e.preventDefault();
      drop.classList.add("border-paytm-blue");
    });
    drop.addEventListener("dragleave", function () {
      drop.classList.remove("border-paytm-blue");
    });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      drop.classList.remove("border-paytm-blue");
      if (e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener("change", function () {
      if (fileInput.files[0]) pickFile(fileInput.files[0]);
    });
  }

  if (btnBatch) {
    btnBatch.addEventListener("click", async function () {
      if (!batchFile) return;
      btnBatch.disabled = true;
      if (batchProgress) batchProgress.classList.remove("hidden");
      if (batchBar) batchBar.style.width = "10%";
      if (batchStatus) batchStatus.textContent = "Processing… (this may take several minutes)";
      if (batchDl) batchDl.classList.add("hidden");
      var fd = new FormData();
      fd.append("file", batchFile);
      try {
        var res = await fetch("/generate/batch", { method: "POST", body: fd });
        if (!res.ok) {
          var err = await res.json().catch(function () {
            return {};
          });
          throw new Error(err.detail || res.statusText);
        }
        if (batchBar) batchBar.style.width = "100%";
        var blob = await res.blob();
        var url = URL.createObjectURL(blob);
        if (batchDl) {
          batchDl.href = url;
          batchDl.download = "vra_batch_reports.zip";
          batchDl.classList.remove("hidden");
        }
        if (batchStatus) {
          var errH = res.headers.get("X-VRA-Batch-Errors");
          batchStatus.textContent =
            "ZIP ready." + (errH && errH !== "0" ? " Some rows failed (" + errH + ")." : "");
        }
        showToast("Batch ZIP downloaded", true);
      } catch (e) {
        showToast(e.message || String(e), false);
      } finally {
        btnBatch.disabled = false;
      }
    });
  }
})();
