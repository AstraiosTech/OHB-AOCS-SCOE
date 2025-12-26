/**
 * Aurora SOCC - Theme Switcher
 * Allows toggling between Aurora (cool) theme and NASA-compliant theme
 */

(function() {
    'use strict';

    // Theme definitions
    const THEMES = {
        aurora: {
            name: 'Aurora',
            description: 'Modern space theme with animations',
            className: ''
        },
        nasa: {
            name: 'NASA',
            description: 'NASA-STD-3001 compliant display',
            className: 'nasa-theme'
        }
    };

    // Current theme
    let currentTheme = localStorage.getItem('socc-theme') || 'aurora';

    /**
     * Apply theme to document
     */
    function applyTheme(themeName) {
        const theme = THEMES[themeName];
        if (!theme) return;

        // Remove all theme classes
        Object.values(THEMES).forEach(t => {
            if (t.className) {
                document.body.classList.remove(t.className);
            }
        });

        // Apply new theme class
        if (theme.className) {
            document.body.classList.add(theme.className);
        }

        // Update active button
        document.querySelectorAll('.theme-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.theme === themeName);
        });

        // Save preference
        currentTheme = themeName;
        localStorage.setItem('socc-theme', themeName);

        // Log theme change
        console.log(`Theme switched to: ${theme.name}`);
        
        // Dispatch event for other components
        window.dispatchEvent(new CustomEvent('themechange', { 
            detail: { theme: themeName } 
        }));
    }

    /**
     * Create theme switcher UI
     */
    function createThemeSwitcher() {
        // Check if already exists
        if (document.querySelector('.theme-switcher')) return;

        const switcher = document.createElement('div');
        switcher.className = 'theme-switcher';
        switcher.setAttribute('role', 'toolbar');
        switcher.setAttribute('aria-label', 'Theme selection');

        // Create label
        const label = document.createElement('span');
        label.textContent = 'Theme: ';
        label.style.cssText = 'color: #888; font-size: 12px; margin-right: 5px;';
        switcher.appendChild(label);

        // Create buttons for each theme
        Object.entries(THEMES).forEach(([key, theme]) => {
            const btn = document.createElement('button');
            btn.className = 'theme-btn';
            btn.dataset.theme = key;
            btn.textContent = theme.name;
            btn.title = theme.description;
            btn.setAttribute('aria-pressed', key === currentTheme);
            
            btn.addEventListener('click', () => {
                applyTheme(key);
                
                // Update aria-pressed
                document.querySelectorAll('.theme-btn').forEach(b => {
                    b.setAttribute('aria-pressed', b.dataset.theme === key);
                });
            });

            if (key === currentTheme) {
                btn.classList.add('active');
            }

            switcher.appendChild(btn);
        });

        document.body.appendChild(switcher);
    }

    /**
     * Initialize theme system
     */
    function init() {
        // Apply saved theme
        applyTheme(currentTheme);

        // Create switcher UI
        createThemeSwitcher();
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose API
    window.SOCCTheme = {
        apply: applyTheme,
        current: () => currentTheme,
        list: () => Object.keys(THEMES)
    };

})();










