const statsGrid = document.getElementById('statsGrid');

function renderStats(stats) {
    statsGrid.innerHTML = `
        <div class="stat-card">
            <h2>Заявки</h2>
            <div class="value">${stats.applications.total}</div>
            <ul>
                <li>Сегодня: ${stats.applications.today}</li>
                <li>За неделю: ${stats.applications.week}</li>
                <li>За 24 часа: ${stats.applications.daily}</li>
                <li>За час: ${stats.applications.hour}</li>
            </ul>
        </div>
        <div class="stat-card">
            <h2>Посещения</h2>
            <div class="value">${stats.visits.total_visits}</div>
            <ul>
                <li>Уникальные: ${stats.visits.unique_visitors}</li>
                <li>Сегодня: ${stats.visits.today_visits}</li>
            </ul>
        </div>
        <div class="stat-card">
            <h2>Роли</h2>
            <ul>
                ${Object.entries(stats.applications.roles)
                    .map(([role, count]) => `<li>${role}: ${count}</li>`)
                    .join('')}
            </ul>
            <p>Популярная роль: <strong>${stats.applications.popular_role}</strong></p>
            <p>Средние часы: <strong>${stats.applications.avg_playtime}</strong></p>
        </div>
        <div class="stat-card">
            <h2>Система</h2>
            <ul>
                <li>Сервер: ${stats.services.server}</li>
                <li>Порт: ${stats.services.server_port}</li>
                <li>База данных: ${stats.services.database}</li>
                <li>Активные сессии: ${stats.system.active_sessions}</li>
                <li>Обновлено: ${stats.system.timestamp}</li>
            </ul>
        </div>
    `;
}

async function loadStats() {
    try {
        const response = await fetch('/api/statistics');
        if (!response.ok) {
            throw new Error('Не удалось получить статистику');
        }
        const data = await response.json();
        renderStats(data);
    } catch (error) {
        console.error(error);
        statsGrid.innerHTML = `
            <div class="stat-card">
                <h2>Ошибка</h2>
                <p>Не удалось загрузить статистику. Попробуйте позже.</p>
            </div>
        `;
    }
}

document.addEventListener('DOMContentLoaded', loadStats);
