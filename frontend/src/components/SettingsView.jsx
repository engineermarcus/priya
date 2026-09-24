import React, { useState } from 'react';

export default function SettingsView() {
  const [notifications, setNotifications] = useState(true);
  const [autoSave, setAutoSave] = useState(true);
  const [theme, setTheme] = useState('System');
  const [saved, setSaved] = useState(false);

  const handleSave = (e) => {
    e.preventDefault();
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <div className="py-10 px-4 sm:px-6 lg:px-8 max-w-4xl mx-auto space-y-8">
      <div>
        <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 dark:text-white">Account Settings</h2>
        <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">Configure your preferences, notifications, and appearance.</p>
      </div>

      <form onSubmit={handleSave} className="space-y-6">
        <div className="bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 rounded-2xl shadow-sm p-6 sm:p-8 space-y-6">
          <h3 className="text-lg font-bold text-slate-900 dark:text-white border-b border-slate-100 dark:border-slate-700 pb-4">General Preferences</h3>

          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <label className="text-sm font-semibold text-slate-900 dark:text-white">Push Notifications</label>
              <p className="text-xs text-slate-500">Receive real-time alerts about deployments and task updates.</p>
            </div>
            <button
              type="button"
              onClick={() => setNotifications(!notifications)}
              className={`w-12 h-6 flex items-center rounded-full p-1 transition-colors ${notifications ? 'bg-indigo-600' : 'bg-slate-300 dark:bg-slate-700'}`}
            >
              <div className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${notifications ? 'translate-x-6' : 'translate-x-0'}`}></div>
            </button>
          </div>

          <div className="flex items-center justify-between pt-4 border-t border-slate-100 dark:border-slate-700/60">
            <div className="space-y-0.5">
              <label className="text-sm font-semibold text-slate-900 dark:text-white">Auto-Save Changes</label>
              <p className="text-xs text-slate-500">Automatically save workspace configurations as you type.</p>
            </div>
            <button
              type="button"
              onClick={() => setAutoSave(!autoSave)}
              className={`w-12 h-6 flex items-center rounded-full p-1 transition-colors ${autoSave ? 'bg-indigo-600' : 'bg-slate-300 dark:bg-slate-700'}`}
            >
              <div className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${autoSave ? 'translate-x-6' : 'translate-x-0'}`}></div>
            </button>
          </div>

          <div className="pt-4 border-t border-slate-100 dark:border-slate-700/60 space-y-2">
            <label className="text-sm font-semibold text-slate-900 dark:text-white block">Theme Mode</label>
            <select
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
              className="w-full sm:w-64 px-4 py-2.5 rounded-xl text-sm bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="System">System Default</option>
              <option value="Light">Light Mode</option>
              <option value="Dark">Dark Mode</option>
            </select>
          </div>
        </div>

        <div className="flex items-center justify-between pt-2">
          {saved ? (
            <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-400">✨ Settings saved successfully!</span>
          ) : (
            <span></span>
          )}
          <button
            type="submit"
            className="px-6 py-3 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-indigo-600 to-violet-600 hover:opacity-95 shadow-lg shadow-indigo-600/20 transition-all"
          >
            Save Changes
          </button>
        </div>
      </form>
    </div>
  );
}
