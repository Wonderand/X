document.addEventListener('submit', (event) => {
  const form = event.target;
  if (form.action.endsWith('/actions/pause') && !window.confirm('暂停后将拦截本地任务，并请求取消远端未来排期。继续吗？')) {
    event.preventDefault();
    return;
  }
  if (form.action.endsWith('/actions/schedule') && !window.confirm('确认将此版本提交到已配置的 X 账号排期？')) {
    event.preventDefault();
    return;
  }
  const button = event.submitter;
  if (button) {
    // Delay disabling so the form's native validation and serialization finish first.
    window.setTimeout(() => { button.disabled = true; button.textContent = '处理中…'; }, 0);
  }
});
