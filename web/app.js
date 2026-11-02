const form = document.querySelector('#listing-form');
const images = document.querySelector('#images');
const imageCount = document.querySelector('#image-count');
const preview = document.querySelector('#preview');
const previewContent = document.querySelector('#preview-content');
const toast = document.querySelector('#toast');
const cinemaMode = document.querySelector('#cinema-mode');
const cinemaTooltip = document.querySelector('#cinema-tooltip');
const renderProgress = document.querySelector('#render-progress');

const tell = (message) => {
  toast.textContent = message;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 4500);
};

images.addEventListener('change', () => {
  imageCount.textContent = `${images.files.length} image${images.files.length === 1 ? '' : 's'} selected`;
});

// Enable Cinema Mode only when the local GPU can run Stable Video Diffusion.
async function checkSvdStatus() {
  try {
    const response = await fetch('/api/svd-status');
    const status = await response.json();
    if (status.available) {
      cinemaMode.disabled = false;
      cinemaTooltip.textContent = `GPU ready (${status.vram_gb} GB)`;
    } else {
      cinemaMode.disabled = true;
      cinemaMode.checked = false;
      cinemaTooltip.textContent = 'Cinema Mode requires a CUDA GPU with 6 GB+ VRAM';
    }
  } catch (_) {
    cinemaMode.disabled = true;
    cinemaTooltip.textContent = 'Cinema Mode requires a CUDA GPU with 6 GB+ VRAM';
  }
}
document.addEventListener('DOMContentLoaded', checkSvdStatus);

const setRenderProgress = (visible) => {
  renderProgress.hidden = !visible;
};

function listing() {
  const data = new FormData(form);
  return {
    property_name: data.get('property_name'),
    location: data.get('location'),
    price: data.get('price'),
    property_type: data.get('property_type'),
    features: String(data.get('features')).split(',').map(x => x.trim()).filter(Boolean),
    cta: data.get('cta'),
  };
}

function validDetails() {
  return form.reportValidity() && listing().features.length > 0;
}

function display(data) {
  previewContent.className = '';

  const qualityHtml = data.image_reports
    ? `<div class="quality-report">
        <p class="eyebrow">IMAGE QUALITY CHECK</p>
        ${data.image_reports.map(r => `
          <div class="quality-item ${r.issues.length ? 'has-issues' : 'ok'}">
            <span class="quality-name">${r.filename}</span>
            ${r.issues.length
              ? r.issues.map(i => `<span class="quality-issue">&#9888; ${i}</span>`).join('')
              : '<span class="quality-ok">&#10003; Looks good</span>'}
          </div>`).join('')}
      </div>`
    : '';

  const captionHtml = `
    <p class="eyebrow">FACT-SAFE SCENE PLAN</p>
    <h2>${data.ai_available ? 'AI-assisted outline' : 'Template outline'}</h2>
    ${data.ai_intro ? `<p class="ai-note">${data.ai_intro}</p>` : ''}
    <ol class="caption-list">${data.captions.map(c => `<li>${c}</li>`).join('')}</ol>
    ${data.suggestions && data.suggestions.length
      ? `<ul class="suggestion-list">${data.suggestions.map(s => `<li>${s}</li>`).join('')}</ul>`
      : ''}
  `;

  const videoHtml = data.download_url
    ? `<div class="video-wrap">
        <video class="video-player" src="${data.download_url}" controls playsinline></video>
       </div>`
    : '';

  previewContent.innerHTML = qualityHtml + captionHtml + videoHtml;
}

preview.addEventListener('click', async () => {
  if (!validDetails()) return;
  preview.disabled = true;
  preview.textContent = 'Preparing\u2026';
  try {
    // Step 1: always fetch captions
    const scriptResp = await fetch('/api/preview-script', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(listing()),
    });
    const scriptData = await scriptResp.json();
    if (!scriptResp.ok) throw Error(scriptData.detail || 'Could not create preview.');

    let mergedData = { ...scriptData };

    // Step 2: if images are selected, also run quality analysis
    if (images.files.length > 0) {
      try {
        const fd = new FormData();
        // Append listing fields individually for the analyse endpoint
        const l = listing();
        fd.append('property_name', l.property_name);
        fd.append('location', l.location);
        fd.append('price', l.price);
        fd.append('property_type', l.property_type);
        fd.append('features', l.features.join(','));
        fd.append('cta', l.cta);
        for (const file of images.files) {
          fd.append('images', file);
        }
        const analyseResp = await fetch('/api/analyse', { method: 'POST', body: fd });
        if (analyseResp.ok) {
          const analyseData = await analyseResp.json();
          mergedData = { ...mergedData, ...analyseData };
        }
      } catch (_) {
        // Quality analysis is best-effort; don't block the preview
      }
    }

    display(mergedData);
  } catch (error) {
    tell(error.message);
  } finally {
    preview.disabled = false;
    preview.textContent = 'Preview script';
  }
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!validDetails()) return;
  if (images.files.length < 4 || images.files.length > 8) {
    return tell('Please upload between 4 and 8 images.');
  }
  const button = form.querySelector('.generate');
  button.disabled = true;
  button.querySelector('span').textContent = 'Rendering video…';
  setRenderProgress(true);
  try {
    const response = await fetch('/api/render', {
      method: 'POST',
      body: new FormData(form),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.detail || 'The video could not be generated.');
    display(data);
    const link = document.createElement('a');
    link.href = data.download_url;
    link.download = 'property-video.mp4';
    link.click();
    tell('Your MP4 is ready. The download has started.');
  } catch (error) {
    tell(error.message);
  } finally {
    setRenderProgress(false);
    button.disabled = false;
    button.querySelector('span').textContent = 'Generate video';
  }
});
