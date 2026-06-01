(function () {
  const toRows = (table) => Array.from(table.querySelectorAll('tbody tr'));
  const textFromRow = (row) => row.innerText.toLowerCase();

  document.querySelectorAll('[data-table-search]').forEach((input) => {
    const tableName = input.getAttribute('data-table-search');
    const table = document.querySelector(`[data-smart-table="${tableName}"]`);
    if (!table) return;
    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      toRows(table).forEach((row) => {
        row.style.display = !q || textFromRow(row).includes(q) ? '' : 'none';
      });
    });
  });

  document.querySelectorAll('table[data-smart-table] th[data-sort]').forEach((th) => {
    th.style.cursor = 'pointer';
    th.addEventListener('click', () => {
      const table = th.closest('table');
      if (!table) return;
      const index = Array.from(th.parentElement.children).indexOf(th);
      const tbody = table.querySelector('tbody');
      if (!tbody) return;
      const rows = toRows(table).filter((r) => r.style.display !== 'none');
      const asc = th.getAttribute('data-order') !== 'asc';
      rows.sort((a, b) => {
        const av = (a.children[index]?.innerText || '').trim();
        const bv = (b.children[index]?.innerText || '').trim();
        return asc ? av.localeCompare(bv, undefined, { numeric: true }) : bv.localeCompare(av, undefined, { numeric: true });
      });
      th.setAttribute('data-order', asc ? 'asc' : 'desc');
      rows.forEach((r) => tbody.appendChild(r));
    });
  });

  document.querySelectorAll('[data-export-csv]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tableName = btn.getAttribute('data-export-csv');
      const table = document.querySelector(`[data-smart-table="${tableName}"]`);
      if (!table) return;
      const rows = Array.from(table.querySelectorAll('tr')).map((tr) =>
        Array.from(tr.querySelectorAll('th,td')).map((cell) => {
          const text = (cell.innerText || '').replace(/\n/g, ' ').replace(/"/g, '""');
          return `"${text}"`;
        }).join(',')
      );
      const csv = rows.join('\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${tableName}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    });
  });

  const palette = ['#0b7fa6', '#19a892', '#ffb04d', '#f06f7b', '#5f7db1', '#7f8c8d'];

  const parseSeries = (canvas) => {
    const values = (canvas.getAttribute('data-values') || '')
      .split(',')
      .map((v) => Number(v.trim()))
      .filter((v) => !Number.isNaN(v));
    const labels = (canvas.getAttribute('data-labels') || '').split(',').map((v) => v.trim());
    return { values, labels };
  };

  const prepCanvas = (canvas) => {
    const width = canvas.clientWidth || 360;
    const height = 220;
    canvas.width = width * (window.devicePixelRatio || 1);
    canvas.height = height * (window.devicePixelRatio || 1);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
    return ctx;
  };

  document.querySelectorAll('canvas[data-chart-donut]').forEach((canvas) => {
    const { values } = parseSeries(canvas);
    const total = values.reduce((a, b) => a + b, 0);
    if (!total) return;
    const ctx = prepCanvas(canvas);
    const cx = canvas.clientWidth / 2;
    const cy = 110;
    const r = 70;
    let start = -Math.PI / 2;
    values.forEach((v, i) => {
      const angle = (v / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, start, start + angle);
      ctx.closePath();
      ctx.fillStyle = palette[i % palette.length];
      ctx.fill();
      start += angle;
    });
    ctx.beginPath();
    ctx.arc(cx, cy, 38, 0, Math.PI * 2);
    ctx.fillStyle = '#ffffff';
    ctx.fill();
  });

  document.querySelectorAll('canvas[data-chart-bars]').forEach((canvas) => {
    const { values } = parseSeries(canvas);
    if (!values.length) return;
    const ctx = prepCanvas(canvas);
    const w = canvas.clientWidth;
    const h = 220;
    const max = Math.max(...values, 1);
    const barW = Math.max(18, Math.min(48, (w - 50) / values.length - 12));
    values.forEach((v, i) => {
      const x = 30 + i * (barW + 18);
      const barH = (v / max) * 145;
      const y = h - 30 - barH;
      ctx.fillStyle = palette[i % palette.length];
      ctx.fillRect(x, y, barW, barH);
      ctx.fillStyle = '#39556a';
      ctx.font = '12px sans-serif';
      ctx.fillText(String(v), x + 2, y - 6);
    });
  });
})();
