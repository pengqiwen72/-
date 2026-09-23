// 主页面 JavaScript
class CsApp {
    constructor() {
        this.apiBaseUrl = '/api';
        this.token = localStorage.getItem('cs_token');
        this.username = localStorage.getItem('cs_username');
        this.currentStream = null;
        this.isProcessing = false;
        this.lastNodeResults = new Map(); // 存储最新的节点结果，避免重复显示
        this.currentTaskActive = false; // 当前是否有活跃任务
        this.taskStartTime = null; // 任务开始时间
        
        this.init();
    }

    init() {
        // 检查登录状态
        if (!this.token) {
            this.redirectToLogin();
            return;
        }

        this.bindEvents();
        this.enableInput();
        this.startInterruptCheck();
    }

    bindEvents() {
        // 登出按钮
        document.getElementById('logoutBtn').addEventListener('click', () => {
            this.logout();
        });

        // 发送按钮
        document.getElementById('sendBtn').addEventListener('click', () => {
            this.sendMessage();
        });

        // 输入框回车发送
        document.getElementById('messageInput').addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // 底部菜单点击
        document.querySelectorAll('.menu-item').forEach(item => {
            item.addEventListener('click', (e) => {
                const menuId = item.dataset.menu;
                this.handleMenuClick(menuId, item);
            });
        });

        // 中断确认按钮
        document.querySelectorAll('.confirm-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const action = btn.dataset.action;
                this.confirmInterrupt(action);
            });
        });
    }

    async logout() {
        try {
            await fetch(`${this.apiBaseUrl}/logout`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.token}`
                }
            });
        } catch (error) {
            console.error('登出请求失败:', error);
        }

        // 清除本地存储
        localStorage.removeItem('cs_token');
        localStorage.removeItem('cs_username');
        
        this.redirectToLogin();
    }

    redirectToLogin() {
        window.location.href = 'login.html';
    }

    enableInput() {
        this.isProcessing = false;
        const messageInput = document.getElementById('messageInput');
        const sendBtn = document.getElementById('sendBtn');
        
        messageInput.disabled = false;
        sendBtn.disabled = false;
        sendBtn.textContent = '发送';
        
        // 恢复输入框的默认提示文字
        messageInput.placeholder = '请输入您的问题...';
        
        // 隐藏进度区域
        document.getElementById('progressArea').style.display = 'none';
        
        // 任务完成，固化当前任务结果到历史
        this.finalizeCurrentTask();
        
        // 清空缓存，为下一轮任务做准备
        this.lastNodeResults.clear();
        this.currentTaskActive = false;
        this.taskStartTime = null;
    }

    disableInput() {
        const messageInput = document.getElementById('messageInput');
        const sendBtn = document.getElementById('sendBtn');
        
        messageInput.disabled = true;
        sendBtn.disabled = true;
        messageInput.placeholder = 'Agent 正在处理中...';
        
        // 清空输入框内容
        messageInput.value = '';
        
        // 显示进度区域
        this.showProgress('正在处理中...');
        
        // 开始新任务
        this.startNewTask();
    }
    
    // 开始新任务
    startNewTask() {
        // 如果已经有活跃任务，不重复创建
        if (this.currentTaskActive) {
            return;
        }
        
        // 添加任务分隔线（如果不是第一个任务）
        const displayArea = document.getElementById('displayArea');
        if (displayArea.children.length > 1) { // 排除欢迎消息
            this.addTaskSeparator();
        }
        
        this.currentTaskActive = true;
        this.taskStartTime = new Date();
        
        // 显示当前任务区域
        document.getElementById('currentTaskArea').style.display = 'block';
        document.getElementById('currentTaskContent').innerHTML = '<div class="task-loading">正在处理任务...</div>';
    }
    
    // 固化当前任务到历史消息
    finalizeCurrentTask() {
        if (!this.currentTaskActive) return;
        
        const currentTaskContent = document.getElementById('currentTaskContent');
        if (currentTaskContent && currentTaskContent.innerHTML.trim()) {
            // 将当前任务内容添加到消息历史
            const displayArea = document.getElementById('displayArea');
            const taskHistoryDiv = document.createElement('div');
            taskHistoryDiv.className = 'message agent task-history';
            taskHistoryDiv.innerHTML = currentTaskContent.innerHTML;
            displayArea.appendChild(taskHistoryDiv);
            displayArea.scrollTop = displayArea.scrollHeight;
        }
        
        // 隐藏当前任务区域
        document.getElementById('currentTaskArea').style.display = 'none';
        this.currentTaskActive = false;
    }
    
    // 添加任务分隔线
    addTaskSeparator() {
        const displayArea = document.getElementById('displayArea');
        const separator = document.createElement('div');
        separator.className = 'task-separator';
        
        // 获取当前时间并格式化为 HH:MM:SS
        const now = new Date();
        const timeString = now.toLocaleTimeString('zh-CN', { 
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
        
        separator.innerHTML = `<span class="task-separator-text">任务执行时间：${timeString}</span>`;
        displayArea.appendChild(separator);
        displayArea.scrollTop = displayArea.scrollHeight;
    }

    async sendMessage() {
        const messageInput = document.getElementById('messageInput');
        const message = messageInput.value.trim();

        if (!message || this.isProcessing) {
            return;
        }

        // 清空输入框并禁用输入
        messageInput.value = '';
        this.disableInput();
        this.isProcessing = true;

        // 显示用户消息
        this.addMessage(message, 'user');

        try {
            await this.streamMessage({ message: message });
        } catch (error) {
            console.error('发送消息失败:', error);
            this.addMessage('发送失败: ' + error.message, 'error');
        } finally {
            this.enableInput();
            this.isProcessing = false;
        }
    }

    async handleMenuClick(menuId, menuElement) {
        if (this.isProcessing) {
            return;
        }

        // 更新菜单状态
        document.querySelectorAll('.menu-item').forEach(item => {
            item.classList.remove('active');
        });
        menuElement.classList.add('active');

        // 更新输入框显示
        const messageInput = document.getElementById('messageInput');
        messageInput.value = menuId;

        // 禁用输入
        this.disableInput();
        this.isProcessing = true;

        // 显示系统消息
        this.addMessage(`正在执行: ${this.getMenuName(menuId)}`, 'system');

        try {
            await this.streamMessage({ menu_id: menuId });
        } catch (error) {
            console.error('菜单操作失败:', error);
            this.addMessage('操作失败: ' + error.message, 'error');
        } finally {
            this.enableInput();
            this.isProcessing = false;
            messageInput.value = '';
            menuElement.classList.remove('active');
        }
    }

    getMenuName(menuId) {
        const menuNames = {
            'profile_suggestion': '生成客户画像',
            'tag_suggestion': '生成客户标签',
            'chat_suggestion': '生成聊天建议',
            'kf_chat_suggestion': '生成客服建议',
            'schedule_suggestion': '生成日程建议'
        };
        return menuNames[menuId] || menuId;
    }

    async streamMessage(data) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/send_message`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.token}`
                },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // 保留不完整的行

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const jsonData = JSON.parse(line.slice(6));
                            this.handleStreamData(jsonData);
                        } catch (e) {
                            console.error('解析流数据失败:', e, line);
                        }
                    }
                }
            }
        } catch (error) {
            throw error;
        }
    }

    handleStreamData(data) {
        console.log('收到流数据:', data);

        if (data.event === 'error') {
            this.addMessage('错误: ' + data.data.error, 'error');
            return;
        }

        if (data.event === 'values' && data.data) {
            // 处理任务结果 - 避免重复显示
            if (data.data.task_result) {
                this.handleTaskResult(data.data.task_result);
            }

            // 处理节点结果 - 只显示最新的进度状态
            if (data.data.node_result && Array.isArray(data.data.node_result)) {
                // 获取最后一个节点结果作为当前进度
                const latestNodeResult = data.data.node_result[data.data.node_result.length - 1];
                if (latestNodeResult) {
                    this.updateProgress(latestNodeResult);
                }
            }
        }
    }

    addMessage(content, type = 'agent') {
        const displayArea = document.getElementById('displayArea');
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}`;
        messageDiv.textContent = content;
        
        displayArea.appendChild(messageDiv);
        displayArea.scrollTop = displayArea.scrollHeight;
    }

    handleTaskResult(taskResult) {
        // 确保当前任务区域可见
        if (!this.currentTaskActive) {
            this.startNewTask();
        }
        
        // 更新固定任务结果区域的内容
        this.updateCurrentTaskContent(taskResult);
    }
    
    // 更新当前任务内容区域
    updateCurrentTaskContent(taskResult) {
        const currentTaskContent = document.getElementById('currentTaskContent');
        if (!currentTaskContent) return;
        
        let contentHtml = '';
        
        // 显示任务结果
        if (taskResult.task_result) {
            // 检查是否为JSON格式的客户画像数据
            if (this.isJsonString(taskResult.task_result)) {
                try {
                    const parsed = JSON.parse(taskResult.task_result);
                    if (parsed.profile_items && Array.isArray(parsed.profile_items)) {
                        // 客户画像特殊处理
                        contentHtml += this.formatProfileContent(parsed.profile_items);
                    } else {
                        // 其他JSON数据格式化显示
                        contentHtml += `<div class="task-result-item">
                            <div class="task-result-main json-content">${JSON.stringify(parsed, null, 2)}</div>
                        </div>`;
                    }
                } catch (e) {
                    // JSON解析失败，按普通文本处理
                    contentHtml += `<div class="task-result-item">
                        <div class="task-result-main">${taskResult.task_result}</div>
                    </div>`;
                }
            } else if (taskResult.task_result.includes('profile_items') || taskResult.task_result.includes('客户profile')) {
                // 可能是包含客户画像的文本，尝试提取JSON部分
                const jsonMatch = taskResult.task_result.match(/\{[\s\S]*\}/);
                if (jsonMatch) {
                    try {
                        const parsed = JSON.parse(jsonMatch[0]);
                        if (parsed.profile_items && Array.isArray(parsed.profile_items)) {
                            contentHtml += this.formatProfileContent(parsed.profile_items);
                        } else {
                            contentHtml += `<div class="task-result-item">
                                <div class="task-result-main">${taskResult.task_result}</div>
                            </div>`;
                        }
                    } catch (e) {
                        contentHtml += `<div class="task-result-item">
                            <div class="task-result-main">${taskResult.task_result}</div>
                        </div>`;
                    }
                } else {
                    contentHtml += `<div class="task-result-item">
                        <div class="task-result-main">${taskResult.task_result}</div>
                    </div>`;
                }
            } else {
                // 普通文本结果
                contentHtml += `<div class="task-result-item">
                    <div class="task-result-main">${taskResult.task_result}</div>
                </div>`;
            }
        }
        
        // 显示说明信息
        if (taskResult.task_result_explain) {
            contentHtml += `<div class="task-result-item">
                <div class="task-result-explain">💡 ${taskResult.task_result_explain}</div>
            </div>`;
        }
        
        // 更新内容（覆盖之前的内容）
        currentTaskContent.innerHTML = contentHtml;
    }
    
    // 格式化客户画像内容
    formatProfileContent(profileItems) {
        let html = `<div class="task-result-item">
            <div class="task-result-main">
                <h4 style="margin: 0 0 10px 0; color: #2e7d32;">📋 客户画像更新成功</h4>
                <div class="profile-table">`;
        
        // 显示所有客户画像信息，不做截断
        profileItems.forEach(item => {
            html += `<div class="profile-row">
                <span class="profile-label">${item.item_name}:</span>
                <span class="profile-value">${item.item_value}</span>
            </div>`;
        });
        
        html += `</div></div></div>`;
        return html;
    }
    
    // 检查字符串是否为JSON格式
    isJsonString(str) {
        try {
            const parsed = JSON.parse(str);
            return typeof parsed === 'object' && parsed !== null;
        } catch (e) {
            return false;
        }
    }
    


    updateProgress(nodeResult) {
        // 检查是否是新的节点结果，避免重复更新
        const nodeKey = `${nodeResult.execute_node_name}_${nodeResult.execute_result_msg}`;
        if (this.lastNodeResults.has(nodeKey)) {
            return; // 已经显示过这个节点结果，跳过
        }
        
        this.lastNodeResults.set(nodeKey, nodeResult);
        
        // 更新进度显示
        const progressText = nodeResult.execute_result_msg || nodeResult.execute_node_name;
        this.showProgress(progressText);
        
        // 如果有异常，显示错误状态
        if (nodeResult.execute_result_code !== 0 || 
            (nodeResult.execute_exceptions && nodeResult.execute_exceptions.length > 0)) {
            this.showProgress(`错误: ${progressText}`, 'error');
        }
    }

    // 中断检查
    async startInterruptCheck() {
        setInterval(async () => {
            if (!this.isProcessing) {
                await this.checkInterrupt();
            }
        }, 3000); // 每3秒检查一次
    }

    async checkInterrupt() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/get_interrupt`, {
                headers: {
                    'Authorization': `Bearer ${this.token}`
                }
            });

            if (response.ok) {
                const data = await response.json();
                if (data.has_interrupt && data.interrupt_info) {
                    this.showInterrupt(data.interrupt_info);
                }
            }
        } catch (error) {
            console.error('检查中断失败:', error);
        }
    }

    showInterrupt(interruptInfo) {
        const interruptArea = document.getElementById('interruptArea');
        const interruptDescription = document.getElementById('interruptDescription');
        
        // 解析中断信息
        let description = '需要您的确认';
        let fullContent = '';
        
        if (interruptInfo && typeof interruptInfo === 'object') {
            // 从中断信息中提取描述和附属内容
            const firstKey = Object.keys(interruptInfo)[0];
            if (firstKey && interruptInfo[firstKey] && interruptInfo[firstKey][0]) {
                const interruptData = interruptInfo[firstKey][0].value;
                if (interruptData) {
                    // 主要描述
                    if (interruptData.description) {
                        description = interruptData.description;
                    }
                    
                    // 构建完整内容，包括附属信息
                    fullContent = description;
                    
                    // 如果有新的客户画像信息，显示预览
                    if (interruptData.new_profile && interruptData.new_profile.profile_items) {
                        const profileItems = interruptData.new_profile.profile_items;
                        fullContent += '\n\n📋 客户画像预览：\n';
                        // 显示前5项关键信息作为预览
                        profileItems.slice(0, 5).forEach(item => {
                            fullContent += `• ${item.item_name}: ${item.item_value}\n`;
                        });
                        if (profileItems.length > 5) {
                            fullContent += `... 还有 ${profileItems.length - 5} 项信息`;
                        }
                    }
                    
                    // 如果有标签建议信息，显示预览
                    if (interruptData.new_tags) {
                        fullContent += '\n\n🏷️ 标签建议预览：\n';
                        
                        // 显示要添加的标签
                        if (interruptData.new_tags.tag_ids_add && interruptData.new_tags.tag_ids_add.length > 0) {
                            fullContent += '\n➕ 建议添加标签：\n';
                            interruptData.new_tags.tag_ids_add.slice(0, 3).forEach(tag => {
                                fullContent += `• ${tag.tag_name} (${tag.tag_id})\n`;
                                if (tag.tag_reason) {
                                    fullContent += `  理由：${tag.tag_reason.substring(0, 50)}...\n`;
                                }
                            });
                            if (interruptData.new_tags.tag_ids_add.length > 3) {
                                fullContent += `... 还有 ${interruptData.new_tags.tag_ids_add.length - 3} 个标签\n`;
                            }
                        }
                        
                        // 显示要移除的标签
                        if (interruptData.new_tags.tag_ids_remove && interruptData.new_tags.tag_ids_remove.length > 0) {
                            fullContent += '\n➖ 建议移除标签：\n';
                            interruptData.new_tags.tag_ids_remove.forEach(tag => {
                                fullContent += `• ${tag.tag_name} (${tag.tag_id})\n`;
                            });
                        }
                    }
                    
                    // 如果有其他附属信息，也可以在这里添加
                    if (interruptData.old_profile && interruptData.old_profile.profile_items && interruptData.old_profile.profile_items.length > 0) {
                        fullContent += '\n\n📝 将替换现有客户信息';
                    }
                    
                    if (interruptData.old_tags && interruptData.old_tags.customer_tags && interruptData.old_tags.customer_tags.length > 0) {
                        fullContent += '\n\n📝 当前客户标签数量：' + interruptData.old_tags.customer_tags.length + ' 个';
                    }
                }
            }
        }
        
        interruptDescription.innerHTML = fullContent.replace(/\n/g, '<br>');
        interruptArea.style.display = 'block';
        
        // 禁用输入
        this.disableInput();
    }

    hideInterrupt() {
        const interruptArea = document.getElementById('interruptArea');
        interruptArea.style.display = 'none';
        
        // 启用输入
        this.enableInput();
    }

    async confirmInterrupt(action) {
        try {
            this.hideInterrupt();
            this.disableInput();
            this.isProcessing = true;

            this.addMessage(`您选择了: ${this.getActionName(action)}`, 'system');

            const response = await fetch(`${this.apiBaseUrl}/confirm_interrupt`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.token}`
                },
                body: JSON.stringify({
                    confirmed: action
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            // 处理流式响应
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const jsonData = JSON.parse(line.slice(6));
                            this.handleStreamData(jsonData);
                        } catch (e) {
                            console.error('解析确认流数据失败:', e, line);
                        }
                    }
                }
            }
        } catch (error) {
            console.error('确认中断失败:', error);
            this.addMessage('确认失败: ' + error.message, 'error');
        } finally {
            this.enableInput();
            this.isProcessing = false;
        }
    }

    getActionName(action) {
        const actionNames = {
            'ok': '确认',
            'discard': '放弃',
            'recreate': '重新生成'
        };
        return actionNames[action] || action;
    }
    
    // 进度显示相关方法
    showProgress(text, type = 'normal') {
        const progressArea = document.getElementById('progressArea');
        const progressText = document.getElementById('progressText');
        const progressIcon = progressArea.querySelector('.progress-icon');
        
        progressText.textContent = text;
        progressArea.style.display = 'block';
        
        // 根据类型设置不同的样式
        if (type === 'error') {
            progressArea.className = 'progress-area error';
            progressIcon.textContent = '❌';
        } else {
            progressArea.className = 'progress-area';
            progressIcon.textContent = '⚡';
        }
    }
    
    hideProgress() {
        const progressArea = document.getElementById('progressArea');
        progressArea.style.display = 'none';
    }
}

// 初始化应用
document.addEventListener('DOMContentLoaded', () => {
    new CsApp();
});
