import React, { useEffect, useState } from 'react';
import axios from 'axios';

/**
 * CulturalCalendar component to display a 3-month forward cultural calendar as a styled list.
 */
const CulturalCalendar = () => {
    const [events, setEvents] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        // Fetch event data from the server
        const fetchEvents = async () => {
            try {
                const response = await axios.get('/api/events/');
                setEvents(response.data);
            } catch (err) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };

        fetchEvents();
    }, []);

    if (loading) return <div>Loading...</div>;
    if (error) return <div>Error: {error}</div>;

    return (
        <ul className="cultural-calendar">
            {Object.entries(events).map(([month, eventList]) => (
                <li key={month}>
                    <h2>{month}</h2>
                    <ul>
                        {eventList.map((event) => (
                            <li key={event.id} className="event-card">
                                <h3>{event.name}</h3>
                                <p>Date: {event.gregorian_date_range}</p>
                                <p>Product Categories: {event.product_categories.join(', ')}</p>
                                <p>Demand Multiplier: {event.demand_multiplier}</p>
                                {event.demand_alert_link && (
                                    <a href={event.demand_alert_link} target="_blank" rel="noopener noreferrer">
                                        Demand Alert
                                    </a>
                                )}
                            </li>
                        ))}
                    </ul>
                </li>
            ))}
        </ul>
    );
};

export default CulturalCalendar;