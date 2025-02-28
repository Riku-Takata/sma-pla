/**
 * 通知センター用JavaScriptファイル
 * WebSocket接続の管理と通知の表示を担当
 */
document.addEventListener('DOMContentLoaded', function() {
  // Socket.IO接続の設定
  const socket = io();
  const connectionStatus = document.getElementById('connection-status');
  const notificationsList = document.getElementById('notifications-list');

  // 接続イベント
  socket.on('connect', function() {
      connectionStatus.textContent = '接続中';
      connectionStatus.className = 'status-indicator online';
      console.log('WebSocket connected');
  });

  // 切断イベント
  socket.on('disconnect', function() {
      connectionStatus.textContent = '切断';
      connectionStatus.className = 'status-indicator offline';
      console.log('WebSocket disconnected');
  });

  // 歓迎メッセージ
  socket.on('welcome', function(data) {
      console.log('Welcome message:', data);
  });

  // 通知イベント
  socket.on('notification', function(data) {
      console.log('Notification received:', data);
      
      // 通知タイプに応じた処理
      if (data.type === 'event') {
          displayEventNotification(data);
          
          // ブラウザ通知
          showBrowserNotification(data);
          
          // 必要に応じてポップアップを開く
          if (data.event_id) {
              openEventPopup(data.event_id);
          }
      } else if (data.type === 'result') {
          displayResultNotification(data);
      } else {
          displayGenericNotification(data);
      }
  });

  /**
   * イベント通知を表示
   * @param {Object} data - イベントデータ
   */
  function displayEventNotification(data) {
      const notificationElement = document.createElement('div');
      notificationElement.className = 'notification-card';
      notificationElement.dataset.eventId = data.event_id || '';
      
      notificationElement.innerHTML = `
          <div class="notification-header">
              <span class="notification-title">${data.summary || '予定'}</span>
              <span class="notification-time">${new Date().toLocaleTimeString()}</span>
          </div>
          <div class="notification-body">
              <p>${data.date || ''} ${data.time || ''}</p>
              ${data.location ? `<p>場所: ${data.location}</p>` : ''}
          </div>
          <div class="notification-actions">
              <button class="approve-btn" data-event-id="${data.event_id}">承認</button>
              <button class="deny-btn" data-event-id="${data.event_id}">拒否</button>
          </div>
      `;
      
      // 通知リストが空の場合はクリア
      clearEmptyState();
      
      // 通知リストの先頭に追加
      notificationsList.insertBefore(notificationElement, notificationsList.firstChild);
      
      // 通知が多すぎる場合、古いものを削除
      limitNotificationsCount(10);
      
      // ボタンにイベントリスナーを設定
      const approveBtn = notificationElement.querySelector('.approve-btn');
      const denyBtn = notificationElement.querySelector('.deny-btn');
      
      approveBtn.addEventListener('click', function() {
          const eventId = this.getAttribute('data-event-id');
          approveEvent(eventId, notificationElement);
      });
      
      denyBtn.addEventListener('click', function() {
          const eventId = this.getAttribute('data-event-id');
          denyEvent(eventId, notificationElement);
      });
  }

  /**
   * 結果通知を表示
   * @param {Object} data - 結果データ
   */
  function displayResultNotification(data) {
      const notificationElement = document.createElement('div');
      notificationElement.className = `notification-card ${data.success ? 'success' : 'error'}`;
      
      notificationElement.innerHTML = `
          <div class="notification-header">
              <span class="notification-title">${data.success ? '✅ 成功' : '❌ 失敗'}</span>
              <span class="notification-time">${new Date().toLocaleTimeString()}</span>
          </div>
          <div class="notification-body">
              <p>${data.message || ''}</p>
          </div>
      `;
      
      // 通知リストが空の場合はクリア
      clearEmptyState();
      
      // 通知リストの先頭に追加
      notificationsList.insertBefore(notificationElement, notificationsList.firstChild);
      
      // 通知が多すぎる場合、古いものを削除
      limitNotificationsCount(10);
  }

  /**
   * 一般的な通知を表示
   * @param {Object} data - 通知データ
   */
  function displayGenericNotification(data) {
      const notificationElement = document.createElement('div');
      notificationElement.className = 'notification-card';
      
      notificationElement.innerHTML = `
          <div class="notification-header">
              <span class="notification-title">${data.title || '通知'}</span>
              <span class="notification-time">${new Date().toLocaleTimeString()}</span>
          </div>
          <div class="notification-body">
              <p>${data.message || ''}</p>
          </div>
      `;
      
      // 通知リストが空の場合はクリア
      clearEmptyState();
      
      // 通知リストの先頭に追加
      notificationsList.insertBefore(notificationElement, notificationsList.firstChild);
      
      // 通知が多すぎる場合、古いものを削除
      limitNotificationsCount(10);
  }

  /**
   * ブラウザ通知を表示
   * @param {Object} data - 通知データ
   */
  function showBrowserNotification(data) {
      // 通知許可の確認
      if (Notification.permission === 'granted') {
          createNotification(data);
      } else if (Notification.permission !== 'denied') {
          Notification.requestPermission().then(permission => {
              if (permission === 'granted') {
                  createNotification(data);
              }
          });
      }
  }

  /**
   * 通知を作成
   * @param {Object} data - 通知データ
   */
  function createNotification(data) {
      let title = '新しい通知';
      let options = {
          body: '',
          icon: '/static/img/calendar-icon.png'
      };
      
      if (data.type === 'event') {
          title = `予定: ${data.summary || '予定'}`;
          options.body = `${data.date || ''} ${data.time || ''}\n${data.location || ''}`;
      } else if (data.type === 'result') {
          title = data.success ? '✅ 予定登録成功' : '❌ 予定登録失敗';
          options.body = data.message || '';
      } else {
          title = data.title || '通知';
          options.body = data.message || '';
      }
      
      const notification = new Notification(title, options);
      
      notification.onclick = function(event) {
          // 通知がクリックされたときにポップアップを開く
          if (data.type === 'event' && data.event_id) {
              openEventPopup(data.event_id);
          }
          notification.close();
      };
      
      // 5秒後に自動で閉じる
      setTimeout(() => {
          notification.close();
      }, 5000);
  }

  /**
   * イベントポップアップを開く
   * @param {string} eventId - イベントID
   */
  function openEventPopup(eventId) {
      // ポップアップウィンドウを開く
      const width = 400;
      const height = 300;
      const left = (screen.width / 2) - (width / 2);
      const top = (screen.height / 2) - (height / 2);
      
      window.open(
          `/event-popup/${eventId}`,
          `event_${eventId}`,
          `width=${width},height=${height},left=${left},top=${top},resizable=no,scrollbars=no,status=no`
      );
  }

  /**
   * イベント承認処理
   * @param {string} eventId - イベントID
   * @param {HTMLElement} notificationElement - 通知要素
   */
  function approveEvent(eventId, notificationElement) {
      if (!eventId) return;
      
      // 承認中の表示
      const actionsContainer = notificationElement.querySelector('.notification-actions');
      actionsContainer.innerHTML = '<div class="spinner-small"></div><span class="status-text">処理中...</span>';
      
      fetch(`/api/event/${eventId}/approve`, {
          method: 'POST',
          headers: {
              'Content-Type': 'application/json'
          }
      })
      .then(response => response.json())
      .then(data => {
          console.log('Approval response:', data);
          
          // 完了表示
          notificationElement.classList.add('approved');
          actionsContainer.innerHTML = '<span class="status-text">✅ 承認済み</span>';
      })
      .catch(error => {
          console.error('Error approving event:', error);
          
          // エラー表示
          actionsContainer.innerHTML = `<span class="status-text error">❌ エラー: ${error.message}</span>`;
      });
  }

  /**
   * イベント拒否処理
   * @param {string} eventId - イベントID
   * @param {HTMLElement} notificationElement - 通知要素
   */
  function denyEvent(eventId, notificationElement) {
      if (!eventId) return;
      
      // 拒否中の表示
      const actionsContainer = notificationElement.querySelector('.notification-actions');
      actionsContainer.innerHTML = '<div class="spinner-small"></div><span class="status-text">処理中...</span>';
      
      fetch(`/api/event/${eventId}/deny`, {
          method: 'POST',
          headers: {
              'Content-Type': 'application/json'
          }
      })
      .then(response => response.json())
      .then(data => {
          console.log('Denial response:', data);
          
          // 完了表示
          notificationElement.classList.add('denied');
          actionsContainer.innerHTML = '<span class="status-text">❌ 拒否済み</span>';
      })
      .catch(error => {
          console.error('Error denying event:', error);
          
          // エラー表示
          actionsContainer.innerHTML = `<span class="status-text error">❌ エラー: ${error.message}</span>`;
      });
  }

  /**
   * 通知リストから空の状態表示を削除
   */
  function clearEmptyState() {
      const emptyState = notificationsList.querySelector('.empty-state');
      if (emptyState) {
          notificationsList.removeChild(emptyState);
      }
  }

  /**
   * 通知リストの数を制限する
   * @param {number} maxCount - 最大表示数
   */
  function limitNotificationsCount(maxCount) {
      while (notificationsList.children.length > maxCount) {
          notificationsList.removeChild(notificationsList.lastChild);
      }
  }
});