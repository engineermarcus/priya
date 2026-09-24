import React, { useState } from 'react';

export default function Hero() {
  const [count, setCount] = useState(0);
  const [email, setEmail] = useState('');
  const [subscribed, setSubscribed] = useState(false);

  const handleSubscribe = (e) => {
    e.preventDefault();
    if (email.trim()) {
      setSubscribed(true);
      setEmail('');
    }
  };

  return (
    <div className="relative overflow-hidden py-12 lg:py-20 px-4 sm:px-6 lg:px-8 bg-gradient-to-b from-slate-50 via-white to-indigo-50/30 dark:from-slate-900 dark:via-slate-900 dark:to-indigo-950/20 border-b border-slate-200 dark:border-slate-800">
      {/* Background glow elements */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full max-w-7xl h-96 bg-gradient-to-tr from-indigo-500/10 via-purple-500/10 to-pink-500/10 blur-3xl pointer-events-none -z-10 rounded-full"></div>

      <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
        <div className="lg:col-span-7 space-y-6 text-center lg:text-left">
          <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full bg-indigo-50 dark:bg-indigo-950/80 border border-indigo-200 dark:border-indigo-800 text-indigo-700 dark:text-indigo-300 text-xs sm:text-sm font-semibold shadow-sm animate-pulse">
            <span className="w-2 h-2 rounded-full bg-indigo-600 dark:bg-indigo-400"></span>
            <span>Next-Generation React Experience</span>
          </div>

          <h1 className="text-4xl sm:text-6xl font-extrabold tracking-tight text-slate-900 dark:text-white leading-[1.1]">
            Build stunning apps with{' '}
            <span className="bg-gradient-to-r from-violet-600 via-indigo-600 to-pink-600 bg-clip-text text-transparent">
              PrismUI & Vite
            </span>
          </h1>

          <p className="text-lg sm:text-xl text-slate-600 dark:text-slate-300 max-w-2xl mx-auto lg:mx-0">
            A lightning-fast, beautifully polished interface designed to supercharge your workflow. Fully responsive, accessible, and ready for production.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center lg:justify-start gap-4 pt-2">
            <button
              onClick={() => setCount((c) => c + 1)}
              className="w-full sm:w-auto px-6 py-3.5 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-indigo-600 via-violet-600 to-pink-600 hover:opacity-95 shadow-lg shadow-indigo-600/25 transform active:scale-95 transition-all flex items-center justify-center space-x-2"
            >
              <span>Interactive Counter:</span>
              <span className="bg-white/20 px-2 py-0.5 rounded-md font-mono">{count}</span>
            </button>
            <a
              href="#features"
              className="w-full sm:w-auto px-6 py-3.5 rounded-xl text-sm font-semibold text-slate-700 dark:text-slate-200 bg-white dark:bg-slate-800 hover:bg-slate-50 dark:hover:bg-slate-700/80 border border-slate-200 dark:border-slate-700 shadow-sm transition-all text-center"
            >
              Explore Features
            </a>
          </div>

          <form onSubmit={handleSubscribe} className="pt-4 max-w-md mx-auto lg:mx-0">
            {subscribed ? (
              <div className="p-3 rounded-xl bg-emerald-50 dark:bg-emerald-950/50 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300 text-sm font-medium text-center">
                ✨ Thank you for subscribing! We'll keep you updated.
              </div>
            ) : (
              <div className="flex gap-2">
                <input
                  type="email"
                  required
                  placeholder="Enter your email for updates..."
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="flex-1 px-4 py-3 rounded-xl text-sm bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 shadow-sm"
                />
                <button
                  type="submit"
                  className="px-5 py-3 rounded-xl text-sm font-semibold bg-slate-900 dark:bg-slate-700 text-white hover:bg-slate-800 dark:hover:bg-slate-600 shadow transition-all shrink-0"
                >
                  Subscribe
                </button>
              </div>
            )}
          </form>
        </div>

        <div className="lg:col-span-5 flex justify-center">
          <div className="relative w-full max-w-md">
            {/* Decorative card mockup */}
            <div className="absolute -inset-1 bg-gradient-to-r from-violet-600 to-pink-600 rounded-3xl blur-xl opacity-30 animate-tilt"></div>
            <div className="relative bg-white dark:bg-slate-800/90 backdrop-blur-xl border border-slate-200/80 dark:border-slate-700/80 rounded-3xl p-6 sm:p-8 shadow-2xl space-y-6">
              <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-700 pb-4">
                <div className="flex items-center space-x-3">
                  <div className="w-3 h-3 rounded-full bg-rose-500"></div>
                  <div className="w-3 h-3 rounded-full bg-amber-500"></div>
                  <div className="w-3 h-3 rounded-full bg-emerald-500"></div>
                </div>
                <span className="text-xs font-mono text-slate-400">preview.tsx</span>
              </div>

              <div className="space-y-4 font-mono text-xs sm:text-sm">
                <div className="p-3 rounded-xl bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-indigo-300 flex items-center justify-between">
                  <span>⚡ Vite HMR Active</span>
                  <span className="text-emerald-500 font-bold">12ms</span>
                </div>
                <div className="p-3 rounded-xl bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-purple-300 flex items-center justify-between">
                  <span>⚛️ React 19 Concurrent</span>
                  <span className="text-violet-500 font-bold">Ready</span>
                </div>
                <div className="p-3 rounded-xl bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-pink-300 flex items-center justify-between">
                  <span>🎨 Tailwind CSS v4</span>
                  <span className="text-pink-500 font-bold">Loaded</span>
                </div>
              </div>

              <div className="pt-2">
                <div className="w-full bg-slate-100 dark:bg-slate-700 rounded-full h-2 overflow-hidden">
                  <div className="bg-gradient-to-r from-violet-600 to-pink-600 h-full w-4/5 rounded-full"></div>
                </div>
                <div className="flex justify-between items-center mt-2 text-xs text-slate-500 dark:text-slate-400">
                  <span>System Performance</span>
                  <span className="font-semibold text-emerald-600 dark:text-emerald-400">99.9% Optimal</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
