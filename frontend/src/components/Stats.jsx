import React from 'react';

export default function Stats() {
  const stats = [
    { label: 'Active Developers', value: '45,000+', change: '+18% this month', positive: true },
    { label: 'Build Speed', value: '< 15ms', change: 'Blazing fast HMR', positive: true },
    { label: 'GitHub Stars', value: '12.8k', change: '+450 this week', positive: true },
    { label: 'Uptime Guarantee', value: '99.99%', change: 'Enterprise grade', positive: true },
  ];

  return (
    <section className="py-16 px-4 sm:px-6 lg:px-8 bg-gradient-to-r from-indigo-900 via-slate-900 to-violet-950 text-white border-b border-slate-800">
      <div className="max-w-7xl mx-auto space-y-12">
        <div className="text-center max-w-2xl mx-auto space-y-3">
          <h3 className="text-xs uppercase tracking-widest text-indigo-400 font-bold">Performance Metrics</h3>
          <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight">Trusted by engineering teams worldwide</h2>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {stats.map((stat, idx) => (
            <div
              key={idx}
              className="p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 hover:border-indigo-500/50 transition-all space-y-3 shadow-xl"
            >
              <div className="text-slate-400 text-sm font-medium">{stat.label}</div>
              <div className="text-3xl sm:text-4xl font-extrabold tracking-tight bg-gradient-to-r from-white via-slate-200 to-indigo-300 bg-clip-text text-transparent">
                {stat.value}
              </div>
              <div className="flex items-center space-x-1.5 text-xs font-semibold text-emerald-400">
                <span>↑</span>
                <span>{stat.change}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
