const galleryList = document.getElementById('galleryList');
const modal = document.getElementById('imageModal');
const modalImage = document.getElementById('modalImage');
const modalCaption = document.getElementById('modalCaption');

async function loadGallery() {
    try {
        const response = await fetch('/gallery-images');
        if (!response.ok) {
            throw new Error('Не удалось загрузить изображения');
        }
        const images = await response.json();
        galleryList.innerHTML = '';

        images.forEach((imageUrl, index) => {
            const galleryItem = document.createElement('div');
            galleryItem.className = 'gallery-item';
            galleryItem.innerHTML = `
                <img src="${imageUrl}" alt="Фото клана BENZ ${index + 1}" loading="lazy">
            `;
            galleryItem.addEventListener('click', () => openModal(imageUrl, index + 1, images.length));
            galleryList.appendChild(galleryItem);
        });
    } catch (error) {
        console.error('Ошибка загрузки галереи:', error);
        galleryList.innerHTML = '<p>Не удалось загрузить галерею.</p>';
    }
}

function openModal(imageUrl, imageNumber, total) {
    modal.style.display = 'block';
    modalImage.src = imageUrl;
    modalCaption.textContent = `Фото клана BENZ (${imageNumber}/${total})`;
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    modal.style.display = 'none';
    document.body.style.overflow = 'auto';
}

function handleScrollButtons() {
    document.querySelectorAll('[data-scroll]').forEach((button) => {
        button.addEventListener('click', () => {
            const distance = Number(button.dataset.scroll);
            galleryList.scrollBy({ left: distance, behavior: 'smooth' });
        });
    });
}

function setupModalHandlers() {
    const closeButton = document.querySelector('[data-action="close"]');
    if (closeButton) {
        closeButton.addEventListener('click', closeModal);
    }

    window.addEventListener('click', (event) => {
        if (event.target === modal) {
            closeModal();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeModal();
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    loadGallery();
    handleScrollButtons();
    setupModalHandlers();
});
