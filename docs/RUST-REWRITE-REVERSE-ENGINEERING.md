# HEX Rust Server 重构 / 客户端逆向第一阶段

## 结论

本阶段以用户提供的完整 `Managed.zip` 为唯一客户端逆向输入，不以旧项目的 Dingler/AI Bridge/Deck Import 架构作为服务器设计依据。

推荐最终服务器：**Rust**。
Python/.NET 仅用于离线逆向分析与代码生成，不作为运行时依赖。

## 已确认的客户端网络结构

### Assembly-CSharp.dll

已确认存在：

- `Game.Shared.Network`
- `Game.Shared.Network.HConnect`
- `Game.Shared.Network.HConnect.Utils`
- `Game.Shared.Network.SFS`
- `Game.Shared.Network.GameSession`
- `Game.Client.Network.GameSession`
- `Game.Shared.Network.Matchmaking`
- `Game.Client.Network.Matchmaking`
- `Game.Shared.Network.Profile`
- `Game.Client.Network.Profile`
- `Game.Shared.Network.LoadBalancer`
- `Game.Client.Network.LoadBalancer`
- `Game.Shared.Network.Escrow`
- `Game.Client.Network.Escrow`
- `Game.Shared.Network.Tournaments`
- `Game.Client.Network.Tournaments`

同时存在：

- `NetworkGameClient`
- `ClientConnection`
- `ConnectionHandler`
- `ConnectionSettings`
- `GameSessionService`
- `SessionClient`
- `ClientSessionBase`
- `AuthoritativeSessionBase`

### HConnect

已发现：

- `SerializeJson`
- `DeserializeJson`
- `CustomNetworkResponse<T>`
- `IServiceConnection`
- `IRequestHandlerSession`

因此目前最可靠的方向是：**先还原 HConnect 的消息封装和服务调用契约，再实现 Rust transport/codec。**

不能仅凭 `RabbitMQ.Client.dll` 推断游戏主协议使用 RabbitMQ；在 `Assembly-CSharp.dll` 的字符串中没有发现足够的 AMQP/RabbitMQ 游戏协议证据。

## GameSession 已确认的请求/响应契约

客户端明确存在：

- StartSession
- StartEncounter
- FindSession
- FindSessionById
- JoinSession
- JoinDisconnectedGame
- FindReconnectionInformation
- ReadyToContinueGame
- ReadyForGameSetup
- ReadyForGameEvents
- ReadyToStartGame
- LeaveSession
- EndSession
- PlayerTransaction
- GetSessionList
- PlayerDisconnected
- Lock
- SetRestart
- SetPlayerUsedTime
- DeclareWinner
- QuitOnReconnectionGame
- TryReconnectionToDisconnectedGame

这说明服务器的第一版 MVP 不应该从“卡牌规则”开始，而应该从：

**Connection → Service Request/Response → Session → Transaction**

开始。

## Matchmaking 已确认

- PingMatchmakingServer
- RequestQuickMatch
- AcceptQuickMatch
- CancelQuickMatch
- ClearMatchMakingServer
- UpdatePlayerRanks
- SendQuickMatchChallenge
- CancelChallenge
- SendChallengeResponse
- RecordRankedGameResults

并存在：

- FoundQuickMatchEventArgs
- SendQuickmatchSessionEventArgs
- FoundChallengeMatchEventArgs
- SendChallangeSessionEventArgs

## Battle State 已确认

`Assembly-CSharp.dll` 中发现完整的 BattleState 家族，包括：

- BattleStateMainPhase
- BattleStateMulligan
- BattleStatePickGoesFirst
- BattleStateDeclareAttackers
- BattleStateDeclareBlockers
- BattleStateAssignBlocker
- BattleStateAssignDamage
- BattleStateAssignCombatDamage
- BattleStateAssignDamageOrder
- BattleStatePriorityBase
- BattleStateInactivePriorityWindow
- BattleStatePlayCard
- BattleStateUseAbility
- BattleStateUseTriggeredAbility
- BattleStateTriggeredAbilities
- BattleStateConfigureAbility
- BattleStateTarget
- BattleStateTargetBase
- BattleStateAssignTargets
- BattleStateAssignOptions
- BattleStateAssignXCost
- BattleStateResourceOptionDialog
- BattleStateResourceXCost
- BattleStateDiscard
- BattleStateGameOver
- BattleStateWait

这意味着可以直接把客户端战斗状态机作为 Rust 服务器规则状态机的逆向规格。

## Transaction 已确认

已发现：

- Transaction
- SubmitTransaction
- SendSessionTransaction
- AssignDamageOrderTransaction
- CommitTroopsToAttackTransaction
- CommitTroopsToDefenseTransaction
- EncounterModDialogTransaction
- DebugCheatTransaction

因此 Rust 服务器应采用：

`Client Request → Validate Transaction → Mutate Authoritative State → Emit Session Events`

而不是允许客户端直接修改 GameState。

## Session Event 已确认

已发现大量服务器状态事件，例如：

- GameStartedSessionEventArgs
- GameEndedSessionEventArgs
- CardDrawnSessionEventArgs
- CardMovedSessionEventArgs
- CardDestroyedSessionEventArgs
- CardDiscardedSessionEventArgs
- CardTappedSessionEventArgs
- CardUntappedSessionEventArgs
- CardPlayed / TroopCardPlayed / SpellCardPlayed / ResourceCardPlayed / ChampionCardPlayed
- AttackDeclaredSessionEventArgs
- BlockersAssignedSessionEventArgs
- BeginCombatResolutionSessionEventArgs
- EndCombatResolutionSessionEventArgs
- CombatPhaseResolvedSessionEventArgs
- TurnPhaseUpdatedSessionEventArgs
- PlayerStateModifiedSessionEventArgs
- PlayerCurrentResourcePoolChangedSessionEventArgs
- PlayerTotalResourcePoolChangedSessionEventArgs
- AbilityActivationDataRequiredSessionEventArgs
- TriggeredAbilityActivationDataRequiredSessionEventArgs
- AbilityPushedOnChainSessionEventArgs
- AbilityCancelledSessionEventArgs
- WaitingOnPlayerSessionEventArgs

这非常适合 Rust 的事件驱动权威服务器。

## Profile 服务

客户端不仅包含卡组接口，还明确暴露了大量 Profile API：

- GetPlayerCardIDList
- GetPlayerDecks
- GetDeckInfo
- GetDeckDetails
- GetDeckIdFromName
- AddNewDeck
- CreateNewDeckFromTemplate
- RemoveDeck
- UpdateDeck
- GetCardDetails
- GetCardFromInstanceId
- GetCardInstanceInfo
- AddCardsToInventory
- AddGemsToInventory
- AddGemsToCard
- GetPlayerChampionsIDList
- AddChampion
- DeleteChampion
- UpdateChampionTalents
- GetChampionInfo
- AddFriend / RemoveFriend / AcceptFriendRequest
- MessageUser
- GetAllFlags / UpdateFlag / UpdateAllFlags

因此 Profile 可以作为第二阶段服务，而不需要一开始把整个商城/社交系统做完。

## Rust 第一版目标

不要复刻旧项目的所有功能。

第一版只实现：

1. TCP/HTTP/HConnect transport 的真实协议形状（继续从 DLL 提取）
2. Login / connection handshake
3. Session create/find/join
4. ReadyForGameSetup / ReadyForGameEvents / ReadyToStartGame
5. PlayerTransaction
6. SessionEvent 广播
7. 最小 BattleState
8. 两人本地测试对局
9. 原版 Unity 客户端连接

暂时不实现：

- Dingler
- Original AI Worker
- Python AI
- Frost Ring Arena
- 商城
- Auction
- Mail
- Tournament
- 复杂 Deck Import

## 最终架构

```
Original Unity Client
        |
        v
Rust HEX Server
  +-- transport
  +-- codec
  +-- services
  +-- sessions
  +-- transactions
  +-- rules
  +-- events
  +-- persistence
```

Python/.NET：

```
Managed DLL -> offline reverse engineering -> protocol/rules specification
```

不会进入服务器运行时。

## 下一阶段

下一步不是写大量业务代码，而是继续从 `Assembly-CSharp-firstpass.dll` 和 `Assembly-CSharp.dll` 提取：

- RequestArgs 字段
- Response 字段
- Service ID / method ID
- HConnect wire format
- Connection handshake
- Session serialization
- Transaction serialization
- Event serialization

拿到这些字段以后，Rust 才开始实现真实协议。

