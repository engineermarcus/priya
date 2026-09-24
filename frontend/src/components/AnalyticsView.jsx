import React from 'react';

export default function AnalyticsView() {
  return (
    <div className="py-10 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto space-y-8">
      <div>
        <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 dark:text-white">Performance Analytics</h2>
        <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">Real-time telemetry, server response times, and user engagement graphs.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div className="p-6 rounded-2xl bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 shadow-sm space-y-6">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-bold text-slate-900 dark:text-white">Traffic & Requests</h3>
            <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 font-semibold border border-emerald-200 dark:border-emerald-800">Live</span>
          </div>
          <div className="h-64 flex items-end justify-between gap-3 pt-4 px-2 bg-slate-50 dark:bg-slate-900/50 rounded-xl border border-slate-100 dark:border-slate-800">
            {[40, 65, 30, 85, 95, 70, 80, 60, 90, 100, 75, 85].map((val, idx) => (
              <div key={idx} className="w-full flex flex-col items-center gap-2">
                <div
                  className="w-full bg-gradient-to-t from-indigo-600 to-violet-500 rounded-t-md transition-all hover:opacity-80"
                  style={{ height: `${val}%` }}
                ></div>
                <span className="text-[10px] text-slate-400 font-mono">M{idx + 1}</span>
              </div>
            ))}
          </div>
          <div className="flex justify-between items-center text-xs text-slate-500">
            <span>Peak: 12.4k req/sec</span>
            <span>Average: 8.9k req/sec</span>
          </div>
        </div>

        <div className="p-6 rounded-2xl bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 shadow-sm space-y-6">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-bold text-slate-900 dark:text-white">Resource Utilization</h3>
            <span className="text-xs px-2.5 py-1 rounded-full bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-200 dark:border-indigo-800">Optimal</span>
          </div>
          <div className="space-y-4 pt-2">
            <div>
              <div className="flex justify-between text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                <span>CPU Load</span>
                <span>38%</span>
              </div>
              <div className="w-full bg-slate-100 dark:bg-slate-700 rounded-full h-2.5">
                <div className="bg-indigo-600 h-2.5 rounded-full w-[38%]"></div>
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                <span>Memory Allocation</span>
                <span>62%</span>
              </div>
              <div className="w-full bg-slate-100 dark:bg-slate-700 rounded-full h-2.5">
                <div className="bg-violet-600 h-2.5 rounded-full w-[62%]"></div>
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                <span>Network Bandwidth</span>
                <span>45%</span>
              </div>
              <div className="w-full bg-slate-100 dark:bg-slate-700 rounded-full h-2.5">
                <div className="bg-pink-600 h-2.5 rounded-full w-[45%]"></div>
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                <span>Storage Capacity</span>
                <span>28%</span>
              </div>
              <div className="w-full bg-slate-100 dark:bg-slate-700 rounded-full h-2.5">
                <div className="bg-emerald-600 h-2.5 rounded-full w-[28%]"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
