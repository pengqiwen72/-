// 登录页面 JavaScript
class LoginManager {
    constructor() {
        this.apiBaseUrl = '/api';
        this.init();
    }

    init() {
        const loginForm = document.getElementById('loginForm');
        const loginBtn = document.getElementById('loginBtn');
        const errorMessage = document.getElementById('errorMessage');

        // 检查是否已经登录
        const token = localStorage.getItem('cs_token');
        if (token) {
            this.redirectToMain();
            return;
        }

        // 绑定登录表单提交事件
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.handleLogin();
        });

        // 回车键登录
        document.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                loginForm.dispatchEvent(new Event('submit'));
            }
        });
    }

    async handleLogin() {
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value.trim();
        const loginBtn = document.getElementById('loginBtn');
        const btnText = loginBtn.querySelector('.btn-text');
        const loadingSpinner = loginBtn.querySelector('.loading-spinner');
        const errorMessage = document.getElementById('errorMessage');

        // 验证输入
        if (!username || !password) {
            this.showError('请输入用户名和密码');
            return;
        }

        // 显示加载状态
        loginBtn.disabled = true;
        btnText.style.display = 'none';
        loadingSpinner.style.display = 'inline';
        this.hideError();

        try {
            const response = await fetch(`${this.apiBaseUrl}/login`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    username: username,
                    password: password
                })
            });

            const data = await response.json();

            if (data.success) {
                // 保存令牌
                localStorage.setItem('cs_token', data.token);
                localStorage.setItem('cs_username', username);
                
                // 跳转到主页面
                this.redirectToMain();
            } else {
                this.showError(data.message || '登录失败');
            }
        } catch (error) {
            console.error('登录错误:', error);
            this.showError('网络连接失败，请检查服务器是否启动');
        } finally {
            // 恢复按钮状态
            loginBtn.disabled = false;
            btnText.style.display = 'inline';
            loadingSpinner.style.display = 'none';
        }
    }

    showError(message) {
        const errorMessage = document.getElementById('errorMessage');
        errorMessage.textContent = message;
        errorMessage.style.display = 'block';
    }

    hideError() {
        const errorMessage = document.getElementById('errorMessage');
        errorMessage.style.display = 'none';
    }

    redirectToMain() {
        window.location.href = 'index.html';
    }
}

// 初始化登录管理器
document.addEventListener('DOMContentLoaded', () => {
    new LoginManager();
});
