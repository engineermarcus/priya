import React from 'react';

export default function ProjectsView() {
  const projects = [
    { name: 'PrismUI Core Library', category: 'React Components', status: 'Active', stars: '4.8k', color: 'from-violet-600 to-indigo-600' },
    { name: 'Vite Speed Runner', category: 'Build Tooling', status: 'In Beta', stars: '2.1k', color: 'from-cyan-500 to-blue-600' },
    { name: 'CloudSync Telemetry', category: 'Backend API', status: 'Active', stars: '3.5k', color: 'from-pink-500 to-rose-600' },
    { name: 'Design System Icons', category: 'Assets', status: 'Archived', stars: '950', color: 'from-amber-500 to-orange-600' },
  ];

  return (
    <div className="py-10 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto space-y-8">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 dark:text-white">Workspace Projects</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">Explore all active repositories and deployed services.</p>
        </div>
        <button
          onClick={() => alert('Create Project modal opened!')}
          className="px-4 py-2.5 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-indigo-600 to-violet-600 hover:opacity-95 shadow-md shadow-indigo-600/20 transition-all self-start md:self-auto"
        >
          + New Project
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {projects.map((proj, idx) => (
          <div
            key={idx}
            className="group p-6 rounded-2xl bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 hover:border-indigo-500 shadow-sm hover:shadow-xl transition-all duration-300 flex flex-col justify-between space-y-6"
          >
            <div className="space-y-4">
              <div className={`w-12 h-12 rounded-xl bg-gradient-to-tr ${proj.color} flex items-center justify-center text-white font-bold text-lg shadow-md group-hover:scale-105 transition-transform`}>
                {proj.name[0]}
              </div>
              <div className="space-y-1">
                <span className="text-xs font-semibold uppercase tracking-wider text-indigo-600 dark:text-indigo-400">{proj.category}</span>
                <h3 className="text-lg font-bold text-slate-900 dark:text-white">{proj.name}</h3>
              </div>
            </div>

            <div className="pt-4 border-t border-slate-100 dark:border-slate-700/60 flex items-center justify-between text-xs">
              <span className={`px-2.5 py-1 rounded-full font-semibold ${proj.status === 'Active' ? 'bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800' : proj.status === 'In Beta' ? 'bg-violet-50 dark:bg-violet-950/60 text-violet-600 dark:text-violet-400 border border-violet-200 dark:border-violet-800' : 'bg-slate-100 dark:bg-slate-700 text-slate-500'}`}>
                {proj.status}
              </span>
              <span className="font-semibold text-slate-500 dark:text-slate-400">⭐ {proj.stars}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
