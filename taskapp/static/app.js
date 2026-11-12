// Live percentage label next to range sliders
document.querySelectorAll('input[type=range][data-output]').forEach((range) => {
  const out = range.parentElement.querySelector('output');
  range.addEventListener('input', () => { if (out) out.textContent = range.value + '%'; });
});

// Confirmation for destructive forms
document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (e) => {
    if (!confirm(form.dataset.confirm)) e.preventDefault();
  });
});

// Task form: show which academic year a task will be filed under
const yearSelect = document.getElementById('yearSelect');
if (yearSelect) {
  const hint = document.getElementById('yearHint');
  const startInput = document.querySelector('[name=start_date]');
  const dueInput = document.querySelector('[name=due_date]');
  const academicYearOf = (iso) => {
    const [y, m] = iso.split('-').map(Number);
    return (m >= 8 ? y : y - 1) + 543;
  };
  const update = () => {
    const ref = startInput.value || dueInput.value;
    if (yearSelect.value === '') {
      hint.textContent = ref ? `จะบันทึกในปีการศึกษา ${academicYearOf(ref)} ตามวันเริ่มต้นงาน` : 'จะบันทึกในปีการศึกษาของวันนี้';
      return;
    }
    const opt = yearSelect.selectedOptions[0];
    const outside = ref && (ref < opt.dataset.start || ref > opt.dataset.end);
    hint.textContent = outside ? '⚠ วันเริ่มต้นงานอยู่นอกช่วงปีการศึกษาที่เลือก' : '';
  };
  [yearSelect, startInput, dueInput].forEach((el) => el.addEventListener('change', update));
  update();
}

// Academic year form: BE year -> 1 Aug (year-543) .. 31 Jul (year-542)
const yearBe = document.querySelector('[data-year-be]');
if (yearBe) {
  yearBe.addEventListener('input', () => {
    const be = Number(yearBe.value);
    if (be < 2400 || be > 2800) return;
    document.querySelector('[data-year-start]').value = `${be - 543}-08-01`;
    document.querySelector('[data-year-end]').value = `${be - 542}-07-31`;
  });
}

// User form: only show divisions (แผนก) that belong to the selected department (หน่วยงาน)
const deptSelect = document.getElementById('deptSelect');
const divSelect = document.getElementById('divSelect');
if (deptSelect && divSelect) {
  const hint = document.getElementById('divHint');
  const filterDivisions = () => {
    const dept = deptSelect.value;
    let available = 0;
    divSelect.querySelectorAll('option[data-dept]').forEach((opt) => {
      const match = opt.dataset.dept === dept;
      opt.hidden = !match;
      opt.disabled = !match;
      if (match) available += 1;
      if (!match && opt.selected) divSelect.value = '';
    });
    divSelect.disabled = !dept || available === 0;
    hint.textContent = !dept ? 'เลือกหน่วยงานก่อน' : (available === 0 ? 'หน่วยงานนี้ยังไม่มีแผนก' : '');
  };
  deptSelect.addEventListener('change', filterDivisions);
  filterDivisions();
}

// Open the report form / sub-task section when linked by hash
if (location.hash.startsWith('#report-')) {
  const el = document.querySelector(location.hash);
  if (el) el.classList.add('flash-target');
}
