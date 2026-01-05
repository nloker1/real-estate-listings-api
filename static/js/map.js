const map = L.map('map', { zoomControl: false }).setView([45.7285, -121.4884], 12);
L.control.zoom({ position: 'topright' }).addTo(map);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap'
}).addTo(map);

let markersLayer = (typeof L.markerclusterGroup === 'function') 
    ? L.markerclusterGroup({ spiderfyOnMaxZoom: true }) 
    : L.layerGroup();
map.addLayer(markersLayer);

async function loadListings(params = {}) {
    console.log("CHECKPOINT 1: loadListings function triggered.");

    try {
        const url = new URL('/api/listings', window.location.origin);
        Object.keys(params).forEach(key => {
            if (params[key]) url.searchParams.append(key, params[key]);
        });

        console.log("CHECKPOINT 2: Fetching from URL:", url.toString());

        const res = await fetch(url);
        if (!res.ok) {
            console.error("API ERROR: Server returned status", res.status);
            return;
        }

        const listings = await res.json();
        console.log("CHECKPOINT 3: Data received. Total listings:", listings.length);
        console.log("RAW DATA SAMPLE:", listings[0]); // Look at the keys here!

        markersLayer.clearLayers();
        const bounds = [];

        listings.forEach((l, index) => {
            // Checkpoint 4: See if the loop is actually running
            if (index === 0) console.log("CHECKPOINT 4: Loop started. First MLS number:", l.mls_number);

            if (l.lat && l.lon) {
                const displayAddress = l.is_address_exposed ? l.address : "Address Withheld";
                
                // Constructing the link - check if l.mls_number exists
                const detailLink = `/listing/${l.mls_number}`;
                
                const content = `
                    <div style="min-width: 160px; font-family: sans-serif;">
                        ${l.photo_url ? `<img src="${l.photo_url}" style="width:100%; height:80px; object-fit:cover; border-radius:4px; margin-bottom:8px;">` : ''}
                        <div style="font-weight: bold; font-size: 14px; color: #1e40af;">$${(l.price || 0).toLocaleString()}</div>
                        <div style="font-size: 12px; color: #4b5563; margin-bottom: 8px;">${displayAddress}</div>
                        <a href="${detailLink}" 
                           style="display: block; text-align: center; background: #2563eb; color: white; text-decoration: none; padding: 6px; border-radius: 4px; font-size: 11px; font-weight: bold;">
                           View Details
                        </a>
                    </div>
                `;

                const m = L.marker([l.lat, l.lon]).bindPopup(content);
                markersLayer.addLayer(m);
                bounds.push([l.lat, l.lon]);
            } else {
                console.warn(`Listing ${l.mls_number} skipped: Missing Lat/Lon`);
            }
        });

        if (bounds.length > 0) {
            console.log("CHECKPOINT 5: Fitting map to bounds.");
            map.fitBounds(bounds, { padding: [50, 50] });
        }
    } catch (err) { 
        console.error("CHECKPOINT ERROR: Map Load Failed:", err); 
    }
}

document.getElementById('searchForm').addEventListener('submit', e => {
    e.preventDefault();
    loadListings({
        city: document.getElementById('city').value,
        min_price: document.getElementById('minPrice').value,
        max_price: document.getElementById('maxPrice').value
    });
});

loadListings();