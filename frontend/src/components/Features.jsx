import React from 'react';

export default function Features() {
  const features = [
    {
      title: 'Lightning Fast Vite',
      description: 'Instant server start and lightning-fast HMR powered by native ESM and Rolldown/Vite architecture.',
      icon: '⚡',
      gradient: 'from-amber-500/20 to-orange-500/20 text-amber-600 dark:text-amber-400',
    },
    {
      title: 'Modern React 19',
      description: 'Leverage the latest concurrent rendering features, Actions, and seamless hooks for maximum performance.',
      icon: '⚛️',
      gradient: 'from-cyan-500/20 to-blue-500/20 text-cyan-600 dark:text-cyan-400',
    },
    {
      title: 'Tailwind CSS v4 Styled',
      description: 'Crafted with utility-first CSS for effortless responsive layouts, gorgeous gradients, and dark mode support.',
      icon: '🎨',
      gradient: 'from-pink-500/20 to-rose-500/20 text-pink-600 dark:text-pink-400',
    },
    {
      title: 'Secure & Scalable',
      description: 'Built with enterprise-grade standards, robust type checking, and modular architecture ready for any scale.',
      icon: '🔒',
      gradient: 'from-emerald-500/20 to-teal-500/20 text-emerald-600 dark:text-emerald-400',
    },
    {
      title: 'Component Library',
      description: 'Includes a rich set of pre-built UI components with smooth animations and interactive states out of the box.',
      icon: '🧩',
      gradient: 'from-violet-500/25 to-indigo-500/25 text-violet-600 dark:text-violet-400',
    },
    {
      title: '24/7 Developer Community',
      description: 'Join thousands of developers building the future of web applications with our active open-source ecosystem.',
      icon: '🚀',
      gradient: 'from-purple-500/20 to-indigo-500/20 text-purple-600 dark:text-purple-400',
    },
  ];

  return (
    <section id="features" className="py-20 px-4 sm:px-6 lg:px-8 bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
      <div className="max-w-7xl mx-auto space-y-16">
        <div className="text-center max-w-3xl mx-auto space-y-4">
          <h2 className="text-xs sm:text-sm font-bold uppercase tracking-wider text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-950/80 inline-block px-3 py-1 rounded-full border border-indigo-200 dark:border-indigo-800">
            Engineered for Excellence
          </h2>
          <h3 className="text-3xl sm:text-5xl font-extrabold text-slate-900 dark:text-white tracking-tight">
            Everything you need to build faster
          </h3>
          <p className="text-base sm:text-lg text-slate-600 dark:text-slate-400">
            Discover the powerful features packed into PrismUI to elevate your web applications from good to exceptional.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          {features.map((feature, idx) => (
            <div
              key={idx}
              className="group relative p-8 rounded-2xl bg-slate-50/80 dark:bg-slate-800/50 border border-slate-200/80 dark:border-slate-700/80 hover:border-indigo-500 dark:hover:border-indigo-500 transition-all duration-300 hover:shadow-xl hover:-translate-y-1 flex flex-col justify-between"
            >
              <div className="space-y-4">
                <div className={`w-14 h-14 rounded-2xl bg-gradient-to-br ${feature.gradient} flex items-center justify-center text-2xl shadow-sm group-hover:scale-110 transition-transform`}>
                  {feature.icon}
                </div>
                <h4 className="text-xl font-bold text-slate-900 dark:text-white">
                  {feature.title}
                </h4>
                <p className="text-slate-600 dark:text-slate-400 text-sm leading-relaxed">
                  {feature.description}
                </p>
              </div>

              <div className="pt-6 mt-6 border-t border-slate-200 dark:border-slate-700/60 flex items-center justify-between text-sm font-semibold text-indigo-600 dark:text-indigo-400 group-hover:translate-x-1 transition-transform cursor-pointer" onClick={() => alert(`Learn more about ${feature.title}`)}>
                <span>Learn more</span>
                <span>→</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
