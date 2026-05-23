document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('searchInput');
    const searchButton = document.getElementById('searchButton');
    const datasetFilter = document.getElementById('datasetFilter');
    const severityFilter = document.getElementById('severityFilter');
    const resultsContainer = document.getElementById('resultsContainer');
    const statusMessage = document.getElementById('statusMessage');

    // Execute search on enter
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            performSearch();
        }
    });

    searchButton.addEventListener('click', performSearch);
    severityFilter.addEventListener('change', () => {
        if (searchInput.value.trim() !== '') {
            performSearch();
        }
    });
    datasetFilter.addEventListener('change', () => {
        if (searchInput.value.trim() !== '') {
            performSearch();
        }
    });

    async function performSearch() {
        const query = searchInput.value.trim();
        const severity = severityFilter.value;
        const collection = datasetFilter.value;
        const apiUrl = `http://127.0.0.1:8000/v1/collections/${collection}/search`;
        
        if (!query) {
            statusMessage.textContent = 'Please enter a search query';
            return;
        }

        // Show loading state
        resultsContainer.innerHTML = `
            <div class="loader-container">
                <div class="loader"></div>
            </div>
        `;
        statusMessage.textContent = 'Searching...';

        const requestBody = {
            query: query,
            top_k: 10
        };

        if (severity !== 'ALL') {
            requestBody.filters = { severity: severity };
        }

        try {
            const startTime = performance.now();
            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                let errorMsg = 'Search failed';
                try {
                    const errorData = await response.json();
                    errorMsg = errorData.detail || errorMsg;
                } catch (e) {}
                throw new Error(`API Error: ${response.status} - ${errorMsg}`);
            }

            const data = await response.json();
            const latency = Math.round(performance.now() - startTime);
            
            renderResults(data.results, latency, data.latency_ms);
        } catch (error) {
            console.error('Search error:', error);
            resultsContainer.innerHTML = `
                <div class="empty-state" style="border-color: rgba(239, 68, 68, 0.3);">
                    <p style="color: #ef4444; margin-bottom: 10px;">⚠️ Search Failed</p>
                    <p style="font-size: 0.9em; color: var(--text-secondary);">${error.message}</p>
                    <p style="font-size: 0.8em; margin-top: 15px;">Make sure the HyperSearch server is running on port 8000 and the 'bench_1000' collection is ingested.</p>
                </div>
            `;
            statusMessage.textContent = 'Error';
        }
    }

    function renderResults(results, totalLatency, backendLatency) {
        if (!results || results.length === 0) {
            resultsContainer.innerHTML = `
                <div class="empty-state">
                    <p>No results found for your query.</p>
                </div>
            `;
            statusMessage.textContent = '0 results';
            return;
        }

        statusMessage.textContent = `${results.length} results (Backend: ${backendLatency.toFixed(1)}ms | Total: ${totalLatency}ms)`;
        
        resultsContainer.innerHTML = '';
        
        results.forEach((result, index) => {
            const meta = result.metadata || {};
            const cve = meta.cve || result.document_id;
            const severity = meta.severity || 'UNKNOWN';
            const cvss = meta.cvss_score || 'N/A';
            
            const sevLower = severity.toLowerCase();
            const badgeClass = ['critical', 'high', 'medium', 'low'].includes(sevLower) ? sevLower : 'unknown';

            // Highlight the query terms (very simple highlighting)
            let desc = result.text || 'No description available';

            const card = document.createElement('div');
            card.className = 'result-card';
            card.style.animationDelay = `${index * 0.05}s`;
            
            card.innerHTML = `
                <div class="card-header">
                    <a href="https://nvd.nist.gov/vuln/detail/${cve}" target="_blank" class="cve-id">${cve}</a>
                    <div class="badges">
                        <span class="badge ${badgeClass}">${severity}</span>
                        <span class="badge badge-score">CVSS <span class="score-val">${cvss}</span></span>
                    </div>
                </div>
                <div class="card-desc">${desc}</div>
                <div class="card-footer">
                    <span>Similarity Score: ${(result.score * 100).toFixed(1)}%</span>
                </div>
            `;
            
            resultsContainer.appendChild(card);
        });
    }
});
