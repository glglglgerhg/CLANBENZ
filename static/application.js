const form = document.getElementById('clanApplicationForm');
const successMessage = document.getElementById('successMessage');
const formCard = document.getElementById('applicationForm');
const submitBtn = document.getElementById('submitBtn');

if (form) {
    form.addEventListener('submit', async (event) => {
        event.preventDefault();

        const playtime = Number(document.getElementById('playtime').value);
        if (Number.isNaN(playtime) || playtime < 1500) {
            alert('Для подачи заявки необходимо минимум 1500 часов в игре.');
            return;
        }

        const formData = new FormData(form);
        const body = new URLSearchParams(formData);

        submitBtn.disabled = true;
        submitBtn.textContent = 'Отправка...';

        try {
            const response = await fetch('/submit_application', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body,
            });

            if (!response.ok) {
                throw new Error('Ошибка отправки');
            }

            const result = await response.json();
            if (result.success) {
                formCard.style.display = 'none';
                successMessage.style.display = 'block';
            } else {
                alert(result.message || 'Не удалось отправить заявку. Попробуйте позже.');
            }
        } catch (error) {
            console.error('Ошибка отправки заявки:', error);
            alert('Не удалось отправить заявку. Попробуйте позже.');
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Отправить заявку';
        }
    });
}
