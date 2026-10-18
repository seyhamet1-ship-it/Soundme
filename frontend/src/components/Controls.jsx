export default function Controls({ selectedLanguage, onLanguageChange, onStart, disabled }) {
  const languages = [
    { value: 'km', label: 'Khmer' },
    { value: 'en', label: 'English' },
    { value: 'fr', label: 'French' },
    { value: 'es', label: 'Spanish' },
    { value: 'de', label: 'German' },
    { value: 'it', label: 'Italian' },
    { value: 'pt', label: 'Portuguese' },
    { value: 'ja', label: 'Japanese' },
    { value: 'ko', label: 'Korean' },
    { value: 'vi', label: 'Vietnamese' },
    { value: 'th', label: 'Thai' },
    { value: 'zh', label: 'Chinese' },
  ];

  return (
    <div className="mt-6 grid gap-4 md:grid-cols-[1fr_auto]">
      <div>
        <label htmlFor="languageSelect" className="mb-2 block text-sm font-medium text-slate-300">
          Target Runkdit ach
        </label>
        <select
          id="languageSelect"
          value={selectedLanguage}
          onChange={(event) => onLanguageChange(event.target.value)}
          className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-base text-slate-100 outline-none transition focus:border-sky-500 focus:ring-2 focus:ring-sky-500/30"
        >
          {languages.map((language) => (
            <option key={language.value} value={language.value}>
              {language.label}
            </option>
          ))}
        </select>
      </div>

      <button
        type="button"
        onClick={onStart}
        disabled={disabled}
        className="self-end rounded-xl bg-sky-500 px-5 py-3 font-semibold text-slate-950 transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Start Translation
      </button>
    </div>
  );
}