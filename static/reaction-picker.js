const emojiPicker = document.querySelector(".emoji-picker");

if (emojiPicker) {
  const emojiInput = document.getElementById("reaction-emoji");
  const emojiSearch = emojiPicker.querySelector(".emoji-search");
  const emptyResult = emojiPicker.querySelector(".emoji-no-results");
  const groups = Array.from(emojiPicker.querySelectorAll(".emoji-group"), section => ({
    section,
    options: section.querySelectorAll(".emoji-option"),
  }));

  function filterEmojis() {
    const query = emojiSearch.value.trim().toLocaleLowerCase().replace(/^:|:$/g, "");
    let found = false;
    for (const { section, options } of groups) {
      let groupFound = false;
      for (const option of options) {
        option.hidden = !option.dataset.name.toLocaleLowerCase().includes(query) &&
          !option.dataset.value.includes(query);
        groupFound ||= !option.hidden;
      }
      section.hidden = !groupFound;
      found ||= groupFound;
    }
    emptyResult.hidden = found;
  }

  emojiSearch.addEventListener("input", filterEmojis);
  emojiPicker.addEventListener("toggle", () => {
    if (emojiPicker.open) emojiSearch.focus({ preventScroll: true });
  });
  emojiPicker.addEventListener("keydown", event => {
    if (event.key === "Escape" && emojiPicker.open) {
      event.preventDefault();
      emojiPicker.open = false;
      emojiPicker.querySelector("summary").focus();
    } else if (event.key === "Enter" && event.target === emojiSearch) {
      event.preventDefault();
    }
  });
  emojiPicker.addEventListener("click", event => {
    const option = event.target.closest(".emoji-option");
    if (!option) return;
    emojiInput.value = option.dataset.value;
    emojiSearch.value = "";
    filterEmojis();
    emojiPicker.open = false;
    emojiInput.focus();
  });
  document.addEventListener("click", event => {
    if (emojiPicker.open && !emojiPicker.contains(event.target)) emojiPicker.open = false;
  });
}
