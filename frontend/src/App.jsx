import { useState } from 'react';
import Navbar from './components/Navbar';
import Hero from './components/Hero';
import Features from './components/Features';
import Stats from './components/Stats';
import DashboardView from './components/DashboardView';
import AnalyticsView from './components/AnalyticsView';
import ProjectsView from './components/ProjectsView';
import SettingsView from './components/SettingsView';
import Footer from './components/Footer';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 text-slate-900 dark:text-slate-100 flex flex-col font-sans selection:bg-indigo-500 selection:text-white">
      <Navbar activeTab={activeTab} setActiveTab={setActiveTab} />

      <main className="flex-1">
        {activeTab === 'dashboard' && (
          <>
            <Hero />
            <Stats />
            <Features />
          </>
        )}
        {activeTab === 'analytics' && <AnalyticsView />}
        {activeTab === 'projects' && <ProjectsView />}
        {activeTab === 'settings' && <SettingsView />}
      </main>

      <Footer />
    </div>
  );
}
