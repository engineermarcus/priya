import React from 'react';

export default function Footer() {
  return (
    <footer className="bg-slate-900 text-slate-400 py-16 px-4 sm:px-6 lg:px-8 border-t border-slate-800">
      <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-5 gap-12 pb-12 border-b border-slate-800">
        <div className="md:col-span-2 space-y-4">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-violet-600 to-pink-500 flex items-center justify-center text-white font-bold text-lg shadow-lg">
              P
            </div>
            <span className="font-extrabold text-xl text-white">PrismUI</span>
          </div>
          <p className="text-sm text-slate-400 max-w-sm leading-relaxed">
            Empowering developers to craft gorgeous, high-performance web applications with React, Vite, and Tailwind CSS.
          </p>
          <div className="flex space-x-4 pt-2">
            <a href="https://github.com" target="_blank" rel="noopener noreferrer" className="w-10 h-10 rounded-xl bg-slate-800 hover:bg-slate-700 flex items-center justify-center text-white transition-colors">
              GH
            </a>
            <a href="https://discord.com" target="_blank" rel="noopener noreferrer" className="w-10 h-10 rounded-xl bg-slate-800 hover:bg-slate-700 flex items-center justify-center text-white transition-colors">
              DC
            </a>
            <a href="https://x.com" target="_blank" rel="noopener noreferrer" className="w-10 h-10 rounded-xl bg-slate-800 hover:bg-slate-700 flex items-center justify-center text-white transition-colors">
              X
            </a>
          </div>
        </div>

        <div className="space-y-4">
          <h4 className="text-white font-semibold text-sm">Product</h4>
          <ul className="space-y-2.5 text-sm">
            <li><a href="#features" className="hover:text-white transition-colors">Features</a></li>
            <li><a href="#analytics" className="hover:text-white transition-colors">Analytics</a></li>
            <li><a href="#projects" className="hover:text-white transition-colors">Projects</a></li>
            <li><a href="#roadmap" className="hover:text-white transition-colors">Roadmap</a></li>
          </ul>
        </div>

        <div className="space-y-4">
          <h4 className="text-white font-semibold text-sm">Resources</h4>
          <ul className="space-y-2.5 text-sm">
            <li><a href="https://react.dev" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">React Documentation</a></li>
            <li><a href="https://vite.dev" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">Vite Guide</a></li>
            <li><a href="https://tailwindcss.com" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">Tailwind CSS</a></li>
            <li><a href="#community" className="hover:text-white transition-colors">Community</a></li>
          </ul>
        </div>

        <div className="space-y-4">
          <h4 className="text-white font-semibold text-sm">Company</h4>
          <ul className="space-y-2.5 text-sm">
            <li><a href="#about" className="hover:text-white transition-colors">About Us</a></li>
            <li><a href="#careers" className="hover:text-white transition-colors">Careers <span className="ml-1.5 px-2 py-0.5 text-xs rounded-full bg-indigo-950 text-indigo-400 border border-indigo-800">We're hiring</span></a></li>
            <li><a href="#privacy" className="hover:text-white transition-colors">Privacy Policy</a></li>
            <li><a href="#terms" className="hover:text-white transition-colors">Terms of Service</a></li>
          </ul>
        </div>
      </div>

      <div className="max-w-7xl mx-auto pt-8 flex flex-col sm:flex-row items-center justify-between text-xs text-slate-500">
        <div>© {new Date().getFullYear()} PrismUI Inc. All rights reserved.</div>
        <div className="flex space-x-6 mt-4 sm:mt-0">
          <a href="#privacy" className="hover:text-slate-400">Privacy</a>
          <a href="#terms" className="hover:text-slate-400">Terms</a>
          <a href="#cookies" className="hover:text-slate-400">Cookies</a>
        </div>
      </div>
    </footer>
  );
}
