document.getElementById('saveSearchBtn').addEventListener('click', async () => {
    const email = prompt("Enter email for listing alerts:");
    if (!email) return;

    const payload = {
        user_email: email,
        city: document.getElementById('city').value,
        min_price: document.getElementById('minPrice').value || null,
        max_price: document.getElementById('maxPrice').value || null
    };

    try {
        const res = await fetch('/api/saved-searches', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) alert("Alert set! We will email you new matches.");
        else alert("Error saving search.");
    } catch (err) { alert("Server error."); }
});