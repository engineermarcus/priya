import React, { useState } from 'react';

export default function DashboardView() {
  const [tasks, setTasks] = useState([
    { id: 1, title: 'Upgrade Vite config to v8', status: 'Completed', priority: 'High', assignee: 'Alex M.' },
    { id: 2, title: 'Implement React 19 concurrent hooks', status: 'In Progress', priority: 'Urgent', assignee: 'Sarah K.' },
    { id: 3, title: 'Refactor Tailwind responsive layouts', status: 'In Progress', priority: 'Medium', assignee: 'David L.' },
    { id: 4, title: 'Write comprehensive test suite', status: 'Pending', priority: 'Low', assignee: 'Emma W.' },
  ]);

  const [newTaskTitle, setNewTaskTitle] = useState('');

  const addTask = (e) => {
    e.preventDefault();
    if (newTaskTitle.trim()) {
      setTasks([
        {
          id: Date.now(),
          title: newTaskTitle,
          status: 'Pending',
          priority: 'Medium',
          assignee: 'You',
        },
        ...tasks,
      ]);
      setNewTaskTitle('');
    }
  };

  const toggleStatus = (id) => {
    setTasks(
      tasks.map((t) => {
        if (t.id === id) {
          const nextStatus =
            t.status === 'Pending'
              ? 'In Progress'
              : t.status === 'In Progress'
              ? 'Completed'
              : 'Pending';
          return { ...t, status: nextStatus };
        }
        return t;
      })
    );
  };

  return (
    <div className="py-10 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto space-y-8">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 dark:text-white">Project Dashboard</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">Manage tasks, monitor active deployments, and track team progress.</p>
        </div>
        <form onSubmit={addTask} className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            placeholder="Add new task..."
            value={newTaskTitle}
            onChange={(e) => setNewTaskTitle(e.target.value)}
            className="w-full sm:w-auto px-4 py-2.5 rounded-xl text-sm bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 shadow-sm"
          />
          <button
            type="submit"
            className="w-full sm:w-auto px-4 py-2.5 rounded-xl text-sm font-semibold bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-600/20 transition-all shrink-0"
          >
            Add Task
          </button>
        </form>
      </div>

      {/* Grid cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="p-6 rounded-2xl bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 shadow-sm space-y-2">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Total Tasks</div>
          <div className="text-3xl font-extrabold text-slate-900 dark:text-white">{tasks.length}</div>
          <div className="text-xs text-indigo-600 dark:text-indigo-400 font-medium">↑ 12% increase this week</div>
        </div>
        <div className="p-6 rounded-2xl bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 shadow-sm space-y-2">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Completed</div>
          <div className="text-3xl font-extrabold text-emerald-600 dark:text-emerald-400">
            {tasks.filter((t) => t.status === 'Completed').length}
          </div>
          <div className="text-xs text-emerald-500 font-medium">On track for release</div>
        </div>
        <div className="p-6 rounded-2xl bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 shadow-sm space-y-2">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">In Progress</div>
          <div className="text-3xl font-extrabold text-violet-600 dark:text-violet-400">
            {tasks.filter((t) => t.status === 'In Progress').length}
          </div>
          <div className="text-xs text-violet-500 font-medium">Active sprints</div>
        </div>
      </div>

      {/* Task table / list */}
      <div className="bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 rounded-2xl shadow-sm overflow-hidden">
        <div className="p-6 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
          <h3 className="text-lg font-bold text-slate-900 dark:text-white">Active Tasks</h3>
          <span className="text-xs text-slate-400">Click status to cycle</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-50 dark:bg-slate-900/50 text-slate-400 text-xs font-semibold uppercase tracking-wider border-b border-slate-200 dark:border-slate-700">
                <th className="py-3 px-6">Task Title</th>
                <th className="py-3 px-6">Status</th>
                <th className="py-3 px-6">Priority</th>
                <th className="py-3 px-6">Assignee</th>
                <th className="py-3 px-6 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700/60 text-sm">
              {tasks.map((task) => (
                <tr key={task.id} className="hover:bg-slate-50/60 dark:hover:bg-slate-700/30 transition-colors">
                  <td className="py-4 px-6 font-medium text-slate-900 dark:text-white">{task.title}</td>
                  <td className="py-4 px-6">
                    <button
                      onClick={() => toggleStatus(task.id)}
                      className={`px-3 py-1 rounded-full text-xs font-semibold transition-all ${
                        task.status === 'Completed'
                          ? 'bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800'
                          : task.status === 'In Progress'
                          ? 'bg-violet-50 dark:bg-violet-950/60 text-violet-600 dark:text-violet-400 border border-violet-200 dark:border-violet-800'
                          : 'bg-amber-50 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800'
                      }`}
                    >
                      {task.status}
                    </button>
                  </td>
                  <td className="py-4 px-6">
                    <span
                      className={`text-xs font-semibold ${
                        task.priority === 'Urgent'
                          ? 'text-rose-600 dark:text-rose-400'
                          : task.priority === 'High'
                          ? 'text-orange-600 dark:text-orange-400'
                          : task.priority === 'Medium'
                          ? 'text-indigo-600 dark:text-indigo-400'
                          : 'text-slate-500'
                      }`}
                    >
                      {task.priority}
                    </span>
                  </td>
                  <td className="py-4 px-6 text-slate-600 dark:text-slate-300">{task.assignee}</td>
                  <td className="py-4 px-6 text-right">
                    <button
                      onClick={() => setTasks(tasks.filter((t) => t.id !== task.id))}
                      className="text-xs text-rose-500 hover:text-rose-700 font-semibold"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
